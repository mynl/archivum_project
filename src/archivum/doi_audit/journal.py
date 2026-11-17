"""
The append-only JSONL files in ``<library>/doi-audit/``.

One line is one record. An append is a single write, so a crash loses at most
the line being written, and a file is readable in any editor.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from pathlib import Path

from . import AUDIT_DIR_NAME

logger = logging.getLogger(__name__)

DECISIONS = "decisions.jsonl"
OBSERVATIONS = "observations.jsonl"
COUNTS = "counts.jsonl"
CITES = "cites.feather"
ACCEPTED, REJECTED = "accepted", "rejected"
OK, NOT_FOUND, ERROR = "ok", "not_found", "error"
CITES_COLUMNS = ["tag", "cites", "cites_asof", "cites_1y", "openalex_id"]

# waitress serves requests on several threads
_LOCK = threading.Lock()
# parsed journal files by path, valid while the file's time and size are unchanged
_PARSED: dict[Path, tuple[tuple[int, int], list[dict]]] = {}


def audit_dir(library_path: Path) -> Path:
    """Return ``<library>/doi-audit``, without creating it."""
    return Path(library_path) / AUDIT_DIR_NAME


def now_utc() -> str:
    """The current time as an ISO string in UTC, to the second."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_lines(library_path: Path, file_name: str, records: list[dict]) -> None:
    """
    Append records to a journal file, creating the folder on first use.

    Parameters
    ----------
    library_path : Path
        The library folder.
    file_name : str
        The journal file, such as :data:`DECISIONS`.
    records : list of dict
        One record per line. A record with no ``at`` is stamped with the time.
    """
    if not records:
        return
    folder = audit_dir(library_path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = now_utc()
    text = "".join(json.dumps({"at": stamp, **record}, ensure_ascii=False) + "\n" for record in records)
    with _LOCK, open(folder / file_name, "a", encoding="utf-8") as f:
        f.write(text)


def read_lines(library_path: Path, file_name: str) -> list[dict]:
    """
    Return the records of a journal file, oldest first.

    A missing file is an empty journal. A line that does not parse, such as
    one cut short by a crash, is skipped and logged. The parsed file is kept
    until the file changes, since one page draw asks for it several times; the
    records are shared, so a caller must not change them.
    """
    path = audit_dir(library_path) / file_name
    try:
        stat = path.stat()
    except OSError:
        return []
    signature = (stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        cached = _PARSED.get(path)
    if cached and cached[0] == signature:
        return list(cached[1])
    records = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipped line %s of %s: not JSON", number, path)
    with _LOCK:
        _PARSED[path] = (signature, records)
    return list(records)


def rejected_proposals(library_path: Path) -> set[tuple]:
    """
    The proposals the user has rejected.

    Returns
    -------
    set of tuple
        ``(tag, field, old, proposed)`` for each rejection. A proposal is
        hidden only while the cell still holds ``old`` and the proposal is
        still ``proposed``.
    """
    return {
        (r.get("tag"), r.get("field"), r.get("old"), r.get("proposed"))
        for r in read_lines(library_path, DECISIONS) if r.get("decision") == REJECTED
    }


def _when(record: dict) -> dt.datetime:
    """The time of a journal record."""
    return dt.datetime.fromisoformat(record["at"])


def fresh_count_keys(library_path: Path, refresh_days: float, now: dt.datetime | None = None) -> set[tuple]:
    """
    The DOI lookups that need not be repeated yet.

    Parameters
    ----------
    library_path : Path
        The library folder.
    refresh_days : float
        A result older than this is stale.
    now : datetime, optional
        The current time, replaced in tests.

    Returns
    -------
    set of tuple
        ``(tag, doi_key)`` for each OpenAlex answer newer than
        ``refresh_days``, whether a count or a not-found. The DOI is part of
        the key, so a reference whose DOI has changed is asked again.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=refresh_days)
    fresh = {(r["tag"], r.get("doi")) for r in read_lines(library_path, COUNTS)
             if r.get("source") == "openalex" and _when(r) >= cutoff}
    fresh |= {(r["tag"], r.get("query")) for r in read_lines(library_path, OBSERVATIONS)
              if r.get("source") == "openalex" and r.get("query_kind") == "doi"
              and r.get("status") == NOT_FOUND and _when(r) >= cutoff}
    return fresh


def derive_cites(library_path: Path):
    """
    Rebuild ``cites.feather`` from ``counts.jsonl``.

    Parameters
    ----------
    library_path : Path
        The library folder.

    Returns
    -------
    pandas.DataFrame
        One row per tag with an OpenAlex count: ``tag``, ``cites`` (the latest
        count), ``cites_asof`` (its date, ISO), ``cites_1y`` and
        ``openalex_id``. Also written to ``<library>/doi-audit/cites.feather``.

    Notes
    -----
    With the latest count :math:`c_1` taken at time :math:`t_1`, ``cites_1y``
    is :math:`c_1 - c_0`, where :math:`c_0` is the most recent count taken at
    or before :math:`t_1 - 365` days. It is missing until such a count exists,
    so it is never a growth over a shorter span passed off as a year's.
    """
    import pandas as pd

    by_tag: dict[str, list[dict]] = {}
    for record in read_lines(library_path, COUNTS):
        if record.get("source") == "openalex" and record.get("count") is not None:
            by_tag.setdefault(record["tag"], []).append(record)

    rows = []
    for tag, records in by_tag.items():
        records.sort(key=_when)
        latest = records[-1]
        year_ago = _when(latest) - dt.timedelta(days=365)
        earlier = [r for r in records if _when(r) <= year_ago]
        rows.append({
            "tag": tag,
            "cites": int(latest["count"]),
            "cites_asof": _when(latest).date().isoformat(),
            "cites_1y": int(latest["count"]) - int(earlier[-1]["count"]) if earlier else None,
            "openalex_id": latest.get("source_id") or "",
        })
    frame = pd.DataFrame(rows, columns=CITES_COLUMNS)
    frame["cites"] = frame["cites"].astype("int64")
    frame["cites_1y"] = frame["cites_1y"].astype("Int64")
    folder = audit_dir(library_path)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (CITES + ".tmp")
    frame.to_feather(tmp)
    os.replace(tmp, folder / CITES)
    return frame


def read_cites(library_path: Path):
    """Return ``cites.feather`` as a frame; an empty frame with the same columns when there is none."""
    import pandas as pd

    path = audit_dir(library_path) / CITES
    if not path.exists():
        return pd.DataFrame(columns=CITES_COLUMNS)
    return pd.read_feather(path)


def latest_observations(library_path: Path) -> dict[tuple, dict]:
    """
    The newest answer of each source to each question.

    Returns
    -------
    dict
        ``(tag, source, query_kind, query) -> observation`` for the newest
        observation whose status is ``ok`` or ``not_found``. An ``error`` is
        not an answer and is left out, so the question is asked again.
    """
    latest: dict[tuple, dict] = {}
    for record in read_lines(library_path, OBSERVATIONS):
        if record.get("status") in (OK, NOT_FOUND):
            latest[(record.get("tag"), record.get("source"), record.get("query_kind"), record.get("query"))] = record
    return latest


def is_fresh(record: dict | None, days: float, now: dt.datetime | None = None) -> bool:
    """Whether a journal record exists and is newer than ``days``."""
    if record is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    return _when(record) >= now - dt.timedelta(days=days)
