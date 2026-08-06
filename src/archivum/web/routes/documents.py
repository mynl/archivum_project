from .shared import *


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

@bp.route('/view-hash/<h>')
def view_hash(h):
    lib = LibraryContext.get()
    path = get_path_for_hash(lib, h)
    if not path or not Path(path).exists(): abort(404)

    # Record read
    lib.record_read(h, caller=request.referrer or "")

    return send_file(path, mimetype=doc_mimetype(path), as_attachment=False)

def get_path_for_tag(lib, tag):
    """Path of the tag's primary document (ref-doc priority 0)."""
    if lib.is_empty: return None
    doc = lib.primary_doc(tag)
    if doc is None: return None
    rel_path = doc.path
    return lib.abspath(rel_path) if hasattr(lib, 'abspath') else rel_path

@bp.route('/view/<tag>')
def view(tag):
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    doc = lib.primary_doc(tag)
    if doc is None: abort(404)

    lib.record_read(doc.hash, caller=request.referrer or "")

    path = lib.abspath(doc.path) if hasattr(lib, 'abspath') else doc.path
    if not path or not Path(path).exists(): abort(404)
    return send_file(path, mimetype=doc_mimetype(path), as_attachment=False)
