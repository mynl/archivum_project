from urllib.parse import quote

from .shared import *

# documents the in-app PDF.js reader opens; anything else goes to the browser
READER_SUFFIXES = {".pdf"}


def doc_mimetype(path) -> str:
    """Best-guess mimetype so epub/djvu are not served as PDF."""
    guess, _ = mimetypes.guess_type(str(path))
    return guess or 'application/octet-stream'


def get_path_for_hash(lib, h):
    if lib.is_empty: return None
    doc_match = lib.doc_df[lib.doc_df['hash'] == h]
    if doc_match.empty: return None
    rel_path = doc_match.iloc[0].path
    return lib.abspath(rel_path) if hasattr(lib, 'abspath') else rel_path


def get_path_for_tag(lib, tag):
    """Path of the tag's primary document (ref-doc priority 0)."""
    if lib.is_empty: return None
    doc = lib.primary_doc(tag)
    if doc is None: return None
    rel_path = doc.path
    return lib.abspath(rel_path) if hasattr(lib, 'abspath') else rel_path


def _send_document(path):
    """
    The raw file. ``conditional`` answers HTTP Range requests with 206: PDF.js
    fetches a large document in chunks.
    """
    return send_file(path, mimetype=doc_mimetype(path), as_attachment=False, conditional=True)


def _render_reader(lib, tag, file_url):
    """The standalone reader page around the vendored PDF.js viewer."""
    title = ""
    if tag:
        rows = lib.ref_df[lib.ref_df.tag == tag]
        if not rows.empty:
            title = clean_latex(str(rows.iloc[0].get("title", "") or "")).strip("{} ")
    short_title = title if len(title) <= 60 else title[:57].rstrip() + "..."
    viewer = url_for('static', filename='vendor/pdfjs/web/viewer.html')
    return render_template(
        'reader.html',
        tag=tag,
        short_title=short_title,
        viewer_url=f"{viewer}?file={quote(file_url, safe='/')}",
    )


@bp.route('/view-hash/<h>')
def view_hash(h):
    lib = LibraryContext.get()
    path = get_path_for_hash(lib, h)
    if not path or not Path(path).exists(): abort(404)

    # a read is one opening of the reader; the raw /file-hash fetches it makes
    # (Range chunks) are not reads
    lib.record_read(h, caller=request.referrer or "")

    if Path(path).suffix.lower() in READER_SUFFIXES:
        tags = lib.ref_doc_df.loc[lib.ref_doc_df.hash == h, "tag"]
        tag = str(tags.iloc[0]) if not tags.empty else h[:8].upper()
        return _render_reader(lib, tag, url_for('main.file_hash', h=h))
    return _send_document(path)


@bp.route('/view/<tag>')
def view(tag):
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    doc = lib.primary_doc(tag)
    if doc is None: abort(404)

    path = lib.abspath(doc.path) if hasattr(lib, 'abspath') else doc.path
    if not path or not Path(path).exists(): abort(404)

    lib.record_read(doc.hash, caller=request.referrer or "")

    if Path(path).suffix.lower() in READER_SUFFIXES:
        return _render_reader(lib, tag, url_for('main.file', tag=tag))
    return _send_document(path)


@bp.route('/file/<tag>', endpoint='file')
def file_for_tag(tag):
    """Raw bytes of the tag's primary document. Not a read: see ``view``."""
    lib = LibraryContext.get()
    path = get_path_for_tag(lib, tag)
    if not path or not Path(path).exists(): abort(404)
    return _send_document(path)


@bp.route('/file-hash/<h>', endpoint='file_hash')
def file_for_hash(h):
    """Raw bytes of a document by hash. Not a read: see ``view_hash``."""
    lib = LibraryContext.get()
    path = get_path_for_hash(lib, h)
    if not path or not Path(path).exists(): abort(404)
    return _send_document(path)


@bp.route('/text/<tag>')
def text(tag):
    """Extracted text of the tag's primary document; full_text_lib mirrors the shard layout."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    doc = lib.primary_doc(tag)
    if doc is None: abort(404)

    rel_path = doc.path
    if not rel_path or pd.isna(rel_path): abort(404)
    path = lib.textpath(rel_path)
    if not path.exists(): abort(404)

    lib.record_read(doc.hash, caller=request.referrer or "")

    # named .md but it is raw extractor output; text/plain renders inline
    return send_file(path, mimetype='text/plain', as_attachment=False,
                     download_name=f"{tag}.txt")
