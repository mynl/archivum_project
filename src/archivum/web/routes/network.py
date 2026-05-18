from .shared import *
from ..services.network import (
    get_semantic_network_payload,
    get_social_network_payload,
)
from ..services.exports import (
    export_dataframe_to_bibtex,
    export_dataframe_to_csv,
    galaxy_export_filename,
)

@bp.route('/network')
def network_page():
    lib = LibraryContext.get()
    return render_template('network.html', lib=lib)

@bp.route('/network-data')
def network_data():
    raw_query = request.args.get('q', '').strip()
    verbosity = request.args.get('verbosity', 'verbose')
    case_sensitive = request.args.get('case') == 'sensitive'
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    try:
        return get_social_network_payload(lib, raw_query, verbosity=verbosity, case_sensitive=case_sensitive)
    except Exception as e:
        logger.error(f"Network error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500

@bp.route('/semantic-data')
def semantic_data():
    raw_query = request.args.get('q', '').strip()
    source_type = request.args.get('source', 'title')
    verbosity = request.args.get('verbosity', 'verbose')
    case_sensitive = request.args.get('case') == 'sensitive'
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    try:
        return get_semantic_network_payload(
            lib,
            raw_query,
            source_type,
            verbosity=verbosity,
            case_sensitive=case_sensitive,
        )
    except Exception as e:
        logger.error(f"Semantic analysis error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500

@bp.route('/semantic-export-csv')
def semantic_export_csv():
    raw_query = request.args.get('q', '').strip()
    source_type = request.args.get('source', 'title')
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query: return "No query provided.", 400

    try:
        result = analyze_semantic(lib, raw_query, source_type)
        if result.result_df.empty:
            return "No matches found.", 400
        if result.relevant_idx.empty:
            return "No cached embeddings for this set.", 400
        export_df = result.to_export_dataframe()

        return export_dataframe_to_csv(export_df, galaxy_export_filename(raw_query))
    except Exception as e:
        logger.error(f"Semantic export error: {e}")
        return str(e), 500


@bp.route('/semantic-export-bibtex')
def semantic_export_bibtex():
    raw_query = request.args.get('q', '').strip()
    source_type = request.args.get('source', 'title')
    plus = request.args.get('plus') == '1'
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query: return "No query provided.", 400

    try:
        result = analyze_semantic(lib, raw_query, source_type)
        if result.result_df.empty:
            return "No matches found.", 400
        if result.relevant_idx.empty:
            return "No cached embeddings for this set.", 400
        return export_dataframe_to_bibtex(result.to_export_dataframe(), lib, plus=plus)
    except Exception as e:
        logger.error(f"Semantic BibTeX export error: {e}")
        return str(e), 500
