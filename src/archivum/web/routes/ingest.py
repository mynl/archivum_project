from .shared import *

from ..services.ingest_batch import (
    BatchError,
    DOC_SUFFIXES,
    cache_prepared,
    counts,
    create_batch,
    discard_batch,
    item,
    item_path,
    load_batch,
    mark,
    next_pending,
    position,
    start_prefetch,
)

# suffixes the browser can render inline in the workbench iframe
VIEWABLE_SUFFIXES = {".pdf"}


# ---------------------------------------------------------------- staging helpers

def _download(url_path: str, dest_dir: Path) -> str:
    """Fetch a URL into dest_dir and return the filename."""
    import requests

    # Robust headers to avoid anti-bot blocks (e.g. from Arxiv)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    # Arxiv specific: if it's an /abs/ link, convert to /pdf/
    if 'arxiv.org/abs/' in url_path:
        url_path = url_path.replace('/abs/', '/pdf/')
        if not url_path.endswith('.pdf'):
            url_path += '.pdf'

    r = requests.get(url_path, stream=True, headers=headers, timeout=30)
    r.raise_for_status()

    if 'html' in r.headers.get('Content-Type', '').lower():
        raise BatchError(
            "URL returned a web page (HTML) instead of a document. "
            "Please provide a direct link to the file."
        )

    filename = url_path.split('/')[-1].split('?')[0] or "downloaded.pdf"
    if Path(filename).suffix.lower() not in DOC_SUFFIXES:
        filename += ".pdf"

    with open(dest_dir / filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return filename


def _prepare(path: Path) -> dict:
    """
    Hash a staged document and discover its metadata.

    JSON-safe so the result can be cached in the batch manifest by the
    prefetch thread. Never raises: a document we cannot read still belongs in
    the queue, the user just has to paste the BibTeX.
    """
    from ...document import Document
    from ...hasher import hash_many3
    from ...bibtex import dict_to_bibtex

    lib = LibraryContext.get()
    out = {"hash": "", "bibtex": "", "candidates": {}, "error": None}

    try:
        out["hash"] = hash_many3([path], workers=lib.config.hash_workers).get(path, "")
    except Exception as e:
        out["error"] = f"Could not hash {path.name}: {e}"
        return out

    try:
        doc = Document(path)
        doc.hash = out["hash"]
        doc.process()
        data = doc.bib.copy()
        if data.get('author'):
            data['author'] = Document._sort_authors(data['author'])
        data['tag'] = doc.key()
        out["bibtex"] = dict_to_bibtex(data)
        # only keep candidates that survive a JSON round trip
        try:
            out["candidates"] = json.loads(json.dumps(doc.candidates, default=str))
        except (TypeError, ValueError):
            out["candidates"] = {}
    except Exception as e:
        # non-PDFs and damaged files land here; the user pastes BibTeX instead
        logger.info("Metadata discovery failed for %s: %s", path.name, e)
        out["error"] = (
            f"Could not extract metadata from {path.name}. Paste the full BibTeX below."
        )

    return out


def _normalize_pasted_bibtex(bibtex: str) -> str:
    """Re-emit pasted BibTeX with author names in Last, First order."""
    from ...document import Document
    from ...bibtex import bibtex_to_dict, dict_to_bibtex

    try:
        entries = bibtex_to_dict(bibtex)
        if entries:
            tag, data = list(entries.items())[0]
            if data.get('author'):
                data['author'] = Document._sort_authors(data['author'])
            data['tag'] = tag
            return dict_to_bibtex(data)
    except Exception:
        pass  # keep original if parsing fails
    return bibtex


# ---------------------------------------------------------------- rendering

def _render_ingest_preview(lib, bibtex, temp_path, known_hash, batch_id, idx):
    try:
        preview = lib.preview_staged_document_import(
            bibtex,
            temp_path,
            known_hash=known_hash,
            source_label="web-ingest-preview",
        )
    except Exception as e:
        # The importer needs a complete entry; a bare or malformed one throws
        # deep inside author/tag mapping. Common for non-PDFs, where discovery
        # finds nothing and the user has to supply the BibTeX.
        logger.error(f"Preview error: {e}")
        preview = {
            "bibtex": bibtex,
            "tag": "",
            "analysis": pd.DataFrame(),
            "blocked": True,
            "blocked_message": (
                f"Could not run the import preview ({e}). Paste a complete BibTeX "
                f"entry with at least author, title, and year, then refresh."
            ),
            "conflict": None,
        }
    return render_template(
        "_ingest_preview.html",
        preview_bibtex=preview["bibtex"],
        preview_tag=preview["tag"],
        preview_blocked=preview["blocked"],
        preview_message=preview["blocked_message"],
        conflict=preview.get("conflict"),
        batch_id=batch_id,
        idx=idx,
        known_hash=known_hash or "",
        analysis_rows=preview["analysis"].to_dict("records") if not preview["analysis"].empty else [],
    )


def _render_workbench(lib, state, idx, bibtex_override=None):
    """Render the workbench for one document of a batch."""
    it = item(state, idx)
    path = item_path(state, idx)

    prepared = it.get("prepared") or _prepare(path)
    if not it.get("prepared"):
        cache_prepared(state["batch_id"], idx, prepared)
        state = load_batch(state["batch_id"])
        it = item(state, idx)

    doc_hash = prepared.get("hash", "")
    bibtex = bibtex_override or prepared.get("bibtex", "")

    # hash-level duplicate check, recomputed each render because the library
    # can change between documents in a batch
    duplicate_tag = None
    if doc_hash and doc_hash in lib.doc_df.hash.values:
        match_tags = lib.ref_doc_df[lib.ref_doc_df.hash == doc_hash].tag.tolist()
        duplicate_tag = match_tags[0] if match_tags else "Unknown"

    preview_html = _render_ingest_preview(
        lib, bibtex, path, doc_hash, state["batch_id"], idx
    )

    return render_template(
        '_ingest_workbench.html',
        lib=lib,
        batch_id=state["batch_id"],
        idx=idx,
        position=position(state, idx),
        counts=counts(state),
        filename=it["filename"],
        origin=it["origin"],
        doc_hash=doc_hash,
        prepare_error=prepared.get("error"),
        candidates=prepared.get("candidates") or {},
        bibtex=bibtex,
        preview_html=preview_html,
        duplicate_tag=duplicate_tag,
        viewable=Path(it["filename"]).suffix.lower() in VIEWABLE_SUFFIXES,
    )


def _banner(level, message=None, html_message=None):
    """A result banner, appended to the log below the workbench."""
    return render_template(
        'components/ingest_result.html',
        level=level,
        message=message,
        html_message=html_message,
    )


def _advance(lib, batch_id, after_idx):
    """Workbench for the next pending document, or the batch summary."""
    state = load_batch(batch_id)
    nxt = next_pending(state, after=after_idx)
    if nxt is not None:
        return _render_workbench(lib, state, nxt)

    c = counts(state)
    discard_batch(batch_id)
    return render_template('_ingest_start.html', lib=lib, summary=c)


# ---------------------------------------------------------------- routes

@bp.route('/ingest')
@admin_required
def ingest_page():
    lib = LibraryContext.get()
    return render_template('ingest.html', lib=lib)


@bp.route('/ingest/start', methods=['POST'])
@admin_required
def ingest_start():
    lib = LibraryContext.get()
    if lib.is_empty:
        abort(400, "No library open")

    bibtex = request.form.get('bibtex', '')
    url_path = request.form.get('url_path', '')
    uploads = request.files.getlist('file')

    try:
        state = create_batch(uploads=uploads, url_path=url_path, downloader=_download)
    except BatchError as e:
        return str(e), 400
    except Exception as e:
        logger.error(f"Staging error: {e}")
        return f"Error staging documents: {e}", 400

    # analyse the rest of the queue while the user works on the first one
    if len(state["items"]) > 1:
        start_prefetch(state["batch_id"], _prepare)

    # pasted BibTeX only makes sense for a single document
    override = None
    if bibtex and len(state["items"]) == 1:
        override = _normalize_pasted_bibtex(bibtex)

    return _render_workbench(lib, state, 0, bibtex_override=override)


@bp.route('/ingest/preview', methods=['POST'])
@admin_required
def ingest_preview():
    lib = LibraryContext.get()
    bibtex = request.form.get('bibtex', '')
    batch_id = request.form.get('batch_id', '')
    idx = request.form.get('idx', '0')
    try:
        state = load_batch(batch_id)
        path = item_path(state, idx)
    except BatchError as e:
        return str(e), 400
    h = request.form.get('hash')
    return _render_ingest_preview(lib, bibtex, path, h, batch_id, idx)


@bp.route('/ingest/commit', methods=['POST'])
@admin_required
def ingest_commit():
    lib = LibraryContext.get()
    bibtex = request.form.get('bibtex')
    batch_id = request.form.get('batch_id', '')
    idx = request.form.get('idx', '0')
    h = request.form.get('hash')

    from ...library import LibraryImportBlocked

    try:
        state = load_batch(batch_id)
        path = item_path(state, idx)
        name = item(state, idx)["filename"]
    except BatchError as e:
        return str(e), 400

    try:
        importer = lib.import_staged_document(
            bibtex, path, known_hash=h, source_label="web-ingest",
        )
        tag = str(importer.ref_df.iloc[0].tag)
        mark(batch_id, idx, "done", tag=tag, hash=h)
        banner = _banner(
            'success',
            html_message=(
                f'<strong>{html.escape(name)}</strong> archived as '
                f'<strong>{html.escape(tag)}</strong> '
                f'<a href="/view/{html.escape(tag, quote=True)}" target="_blank">View</a>'
            ),
        )
    except LibraryImportBlocked as e:
        mark(batch_id, idx, "failed", message=str(e))
        banner = _banner('danger', message=f"{name}: {e}")
    except Exception as e:
        logger.error(f"Commit error: {e}")
        mark(batch_id, idx, "failed", message=str(e))
        banner = _banner('danger', message=f"{name}: Error: {e}")

    return _advance(lib, batch_id, int(idx)) + banner


@bp.route('/ingest/replace', methods=['POST'])
@admin_required
def ingest_replace():
    """Make a staged file the primary document of an existing reference."""
    lib = LibraryContext.get()
    bibtex = request.form.get('bibtex', '')
    batch_id = request.form.get('batch_id', '')
    idx = request.form.get('idx', '0')
    h = request.form.get('hash')
    target_tag = request.form.get('match_tag', '')
    update_meta = request.form.get('update_metadata') == 'on'

    try:
        state = load_batch(batch_id)
        path = item_path(state, idx)
        name = item(state, idx)["filename"]
    except BatchError as e:
        return str(e), 400

    if not target_tag:
        return "No reference given to replace.", 400

    try:
        result = lib.replace_document(
            target_tag,
            path,
            known_hash=h,
            update_bibtex=bibtex if update_meta else None,
        )
        mark(batch_id, idx, "replaced", tag=target_tag, hash=result["hash"])
        extra = " and metadata updated" if update_meta else ""
        banner = _banner(
            'success',
            html_message=(
                f'<strong>{html.escape(name)}</strong> is now the primary document for '
                f'<strong>{html.escape(target_tag)}</strong>{extra} '
                f'<a href="/view/{html.escape(target_tag, quote=True)}" target="_blank">View</a>'
                f'<span class="text-muted ms-2">previous document kept as an alternate</span>'
            ),
        )
    except Exception as e:
        logger.error(f"Replace error: {e}")
        mark(batch_id, idx, "failed", message=str(e))
        banner = _banner('danger', message=f"{name}: Replace failed: {e}")

    return _advance(lib, batch_id, int(idx)) + banner


@bp.route('/ingest/next', methods=['POST'])
@admin_required
def ingest_next():
    """Skip the current document and move to the next."""
    lib = LibraryContext.get()
    batch_id = request.form.get('batch_id', '')
    idx = request.form.get('idx', '0')

    try:
        state = load_batch(batch_id)
        name = item(state, idx)["filename"]
    except BatchError as e:
        return str(e), 400

    mark(batch_id, idx, "skipped")
    banner = _banner('warning', message=f"{name}: skipped, not imported.")
    return _advance(lib, batch_id, int(idx)) + banner


@bp.route('/ingest/cancel', methods=['POST'])
@admin_required
def ingest_cancel():
    """Abandon the whole batch. Staged hard links go; originals do not."""
    lib = LibraryContext.get()
    batch_id = request.form.get('batch_id', '')

    try:
        state = load_batch(batch_id)
        c = counts(state)
    except BatchError:
        c = None

    discard_batch(batch_id)
    message = (
        f"Batch cancelled with {c['pending']} of {c['total']} documents unprocessed."
        if c else "Batch cancelled."
    )
    return render_template('_ingest_start.html', lib=lib, summary=None) + _banner(
        'info', message=message
    )


@bp.route('/view-temp/<batch_id>/<int:idx>')
@admin_required
def view_temp(batch_id, idx):
    """Serve a staged document. Resolved through the manifest, never by name."""
    from .documents import doc_mimetype

    try:
        state = load_batch(batch_id)
        temp_path = item_path(state, idx)
    except BatchError:
        abort(404)
    if not temp_path.exists():
        abort(404)
    return send_file(str(temp_path.absolute()), mimetype=doc_mimetype(temp_path))
