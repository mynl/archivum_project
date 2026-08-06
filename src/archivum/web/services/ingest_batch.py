"""
Staging and queue state for the web Ingest page.

Every ingest is a batch of N documents; a single document is just N=1, so the
one-file and many-file flows share a code path.

State lives on disk, not in a Flask session (the app sets no SECRET_KEY), as
``temp/staging/<batch_id>/manifest.json``. Each document gets its **own**
numbered subdirectory. That is load-bearing, not tidiness:
``Library._make_staged_document_importer`` hands ``doc_dir=<staged>.parent`` to
``Bib2df_Incremental``, which scans and hashes every file it finds there -- on
every preview and every commit. One directory per document keeps that to one
file.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

STAGING_ROOT = Path("temp/staging")

# pymupdf-openable formats plus djvu, which we stage but cannot preview
DOC_SUFFIXES = {".pdf", ".djvu", ".epub", ".mobi", ".chm", ".xps", ".fb2"}

BATCH_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# batch directories older than this are swept on the next batch creation
STALE_BATCH_SECONDS = 7 * 24 * 3600

# Reentrant: mark() needs to hold it across a read-modify-write while
# load_batch/save_batch take it individually. Without that the prefetch thread
# and the request thread can clobber each other's manifest updates.
_MANIFEST_LOCK = threading.RLock()


class BatchError(ValueError):
    """Raised for a malformed or missing batch."""


def batch_dir(batch_id: str) -> Path:
    """Validated path to a batch directory. Rejects anything path-like."""
    if not BATCH_ID_RE.match(batch_id or ""):
        raise BatchError(f"Invalid batch id: {batch_id!r}")
    return STAGING_ROOT / batch_id


def manifest_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / "manifest.json"


def load_batch(batch_id: str) -> dict:
    p = manifest_path(batch_id)
    if not p.exists():
        raise BatchError(f"Batch {batch_id} not found; it may have been cancelled.")
    with _MANIFEST_LOCK:
        return json.loads(p.read_text(encoding="utf-8"))


def save_batch(state: dict) -> None:
    p = manifest_path(state["batch_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with _MANIFEST_LOCK:
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def item(state: dict, idx: int) -> dict:
    try:
        return state["items"][int(idx)]
    except (IndexError, ValueError, TypeError):
        raise BatchError(f"No document {idx} in batch {state.get('batch_id')}")


def item_path(state: dict, idx: int) -> Path:
    it = item(state, idx)
    return batch_dir(state["batch_id"]) / it["dir"] / it["filename"]


def mark(batch_id: str, idx: int, status: str | None = None, **info) -> dict:
    """
    Update one document and persist the manifest.

    ``status=None`` leaves the status alone. The prefetch thread must use that:
    it computes against a snapshot, and writing back the status it saw would
    resurrect an item the user has settled in the meantime.
    """
    with _MANIFEST_LOCK:
        state = load_batch(batch_id)
        it = item(state, idx)
        if status is not None:
            it["status"] = status
        it.update(info)
        save_batch(state)
        return state


def cache_prepared(batch_id: str, idx: int, prepared: dict) -> None:
    """Store discovery results for a document without touching its status."""
    mark(batch_id, idx, None, prepared=prepared)


def next_pending(state: dict, after: int = -1) -> int | None:
    """Index of the next document still awaiting a decision, or None."""
    for it in state["items"]:
        if it["idx"] > after and it["status"] == "pending":
            return it["idx"]
    return None


def counts(state: dict) -> dict:
    out = {"total": len(state["items"])}
    for key in ("pending", "done", "skipped", "failed", "replaced"):
        out[key] = sum(1 for it in state["items"] if it["status"] == key)
    out["settled"] = out["total"] - out["pending"]
    return out


def position(state: dict, idx: int) -> int:
    """1-based position of a document for 'Importing M of N' display."""
    return int(idx) + 1


def _stage_file(source: Path, dest_dir: Path, filename: str) -> None:
    """
    Put ``source`` into ``dest_dir``, preferring a hard link.

    Hard links are how sharding already works here, so this never copies bytes
    for a same-volume source and never touches the original. Cross-volume falls
    back to a copy.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists():
        dest.unlink()
    try:
        dest.hardlink_to(source)
    except OSError:
        shutil.copy2(source, dest)


def _safe_name(name: str) -> str:
    """Filename only, with separators neutralised."""
    return Path(str(name)).name.replace("\\", "_") or "document"


def sweep_stale(max_age: float = STALE_BATCH_SECONDS) -> int:
    """Remove batch directories older than ``max_age``. Returns the count."""
    if not STAGING_ROOT.exists():
        return 0
    cutoff = time.time() - max_age
    removed = 0
    for p in STAGING_ROOT.iterdir():
        if not p.is_dir() or not BATCH_ID_RE.match(p.name):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p)
                removed += 1
        except OSError as e:
            logger.warning("Could not sweep stale batch %s: %s", p, e)
    if removed:
        logger.info("Swept %s stale ingest batches.", removed)
    return removed


def create_batch(uploads=None, url_path: str = "", downloader=None) -> dict:
    """
    Stage every requested document and return the manifest.

    ``uploads``  werkzeug FileStorage objects from a ``multiple`` file input.
    ``url_path`` a single file, a directory (globbed NON-recursively), or a URL.
    ``downloader`` callable(url, dest_dir) -> filename, used for http(s) input
                 so this module stays free of network concerns.
    """
    sweep_stale()

    batch_id = uuid.uuid4().hex[:12]
    root = batch_dir(batch_id)
    root.mkdir(parents=True, exist_ok=True)

    items: list[dict] = []

    def add(filename: str, source: str, origin: str) -> None:
        idx = len(items)
        items.append({
            "idx": idx,
            "dir": f"{idx:02d}",
            "filename": filename,
            "source": source,
            "origin": origin,
            "status": "pending",
            "tag": None,
            "message": None,
            "hash": None,
            "prepared": None,
        })

    for uploaded in uploads or []:
        if not getattr(uploaded, "filename", ""):
            continue
        name = _safe_name(uploaded.filename)
        idx = len(items)
        dest_dir = root / f"{idx:02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        uploaded.save(dest_dir / name)
        add(name, "upload", uploaded.filename)

    url_path = (url_path or "").strip()
    if url_path:
        p = Path(url_path)
        if p.is_dir():
            # this folder only; recursion is deliberately not offered
            found = sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in DOC_SUFFIXES
            )
            if not found:
                shutil.rmtree(root, ignore_errors=True)
                raise BatchError(
                    f"No documents ({', '.join(sorted(DOC_SUFFIXES))}) in {p}"
                )
            for f in found:
                name = _safe_name(f.name)
                _stage_file(f, root / f"{len(items):02d}", name)
                add(name, "folder", str(f))
        elif p.is_file():
            name = _safe_name(p.name)
            _stage_file(p, root / f"{len(items):02d}", name)
            add(name, "path", str(p))
        elif url_path.startswith(("http://", "https://")):
            if downloader is None:
                raise BatchError("No downloader available for URL input.")
            dest_dir = root / f"{len(items):02d}"
            dest_dir.mkdir(parents=True, exist_ok=True)
            name = downloader(url_path, dest_dir)
            add(_safe_name(name), "url", url_path)
        else:
            shutil.rmtree(root, ignore_errors=True)
            raise BatchError(f"Not a file, folder, or URL: {url_path}")

    if not items:
        shutil.rmtree(root, ignore_errors=True)
        raise BatchError("No file, folder, or URL provided.")

    state = {"batch_id": batch_id, "items": items}
    save_batch(state)
    logger.info("Ingest batch %s staged with %s documents.", batch_id, len(items))
    return state


def discard_batch(batch_id: str) -> None:
    """Remove a batch's staging tree. Originals are untouched (hard links)."""
    try:
        root = batch_dir(batch_id)
    except BatchError:
        return
    shutil.rmtree(root, ignore_errors=True)
    logger.info("Discarded ingest batch %s.", batch_id)


def start_prefetch(batch_id: str, prepare, start_at: int = 1) -> None:
    """
    Run ``prepare(path) -> dict`` over the queue on a daemon thread.

    Metadata discovery hits Crossref/arXiv and is slow, so documents further
    down the queue are analysed while the user works on the current one. The
    result is cached in the manifest; a miss just means the route computes it
    inline.

    ``start_at`` defaults to 1 because the caller renders document 0
    immediately and would otherwise do the same work, and the same network
    lookups, twice.
    """
    def run():
        try:
            indices = [it["idx"] for it in load_batch(batch_id)["items"]]
        except BatchError:
            return
        for idx in indices[start_at:]:
            try:
                if not manifest_path(batch_id).exists():
                    return  # batch cancelled underneath us
                fresh = load_batch(batch_id)
                entry = item(fresh, idx)
                if entry.get("prepared") or entry["status"] != "pending":
                    continue
                prepared = prepare(item_path(fresh, idx))
            except BatchError:
                return
            except Exception as e:  # never let prefetch break the batch
                logger.warning("Prefetch failed for %s/%s: %s", batch_id, idx, e)
                continue
            try:
                # status deliberately untouched: the user may have settled this
                # document while we were working on it
                cache_prepared(batch_id, idx, prepared)
            except BatchError:
                return

    threading.Thread(target=run, name=f"ingest-prefetch-{batch_id}", daemon=True).start()
