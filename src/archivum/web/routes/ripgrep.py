from .shared import *
from ..services.ripgrep import (
    RipgrepSearchOptions,
    export_ripgrep_csv,
    get_cached_ripgrep_search,
    ripgrep_export_dataframe,
    stream_ripgrep_search,
    warm_ripgrep_cache,
)
from ..services.exports import export_dataframe_to_bibtex
from ..services.search_history import note_search

@bp.route('/ripgrep')
def ripgrep_page():
    return render_template('ripgrep.html')

@bp.route('/rg-warm')
def rg_warm():
    """Warming route to build metadata cache for Ripgrep."""
    lib = LibraryContext.get()
    if not lib.is_empty:
        warm_ripgrep_cache(lib)
    return "OK"

@bp.route('/rg-search')
def rg_search():
    options = RipgrepSearchOptions.from_request_args(request.args)
    lib = LibraryContext.get()
    # before the no-work and cache returns, so every search, and an empty one, is noted
    if not lib.is_empty:
        note_search(lib, "ripgrep", options.query)
    if not options.has_work:
        return ""

    if lib.is_empty:
        return "No library open."

    cached_html = get_cached_ripgrep_search(lib, options)
    if cached_html is not None:
        return cached_html

    return Response(stream_with_context(stream_ripgrep_search(lib, options)), mimetype='text/html')

@bp.route('/rg-export-csv')
def rg_export_csv():
    query = request.args.get('q', '').strip()
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    return export_ripgrep_csv(lib, query)


@bp.route('/rg-export-bibtex')
def rg_export_bibtex():
    query = request.args.get('q', '').strip()
    plus = request.args.get('plus') == '1'
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    export_df = ripgrep_export_dataframe(lib, query)
    if isinstance(export_df, tuple):
        return export_df
    return export_dataframe_to_bibtex(export_df, lib, plus=plus)

