from .shared import *
from ...search.query import QUERY_HELP_TEXT
from ..services.exports import (
    export_dataframe_to_bibtex,
    export_dataframe_to_csv,
    query_export_filename,
)

@bp.route('/')
def index():
    return render_template('query.html')

def parse_rg_json(proc, lib):
    """Parse ripgrep JSON output and group by document with full metadata."""
    blocks = []
    blocks_by_hash_prefix = {}
    
    # Advanced metadata lookup
    # Pre-map first 10 chars of hash to metadata
    hash_prefix_to_meta = {}
    if not lib.ref_doc_df.empty:
        # Get latest tag for each hash
        latest_links = lib.ref_doc_df.sort_values(['hash', 'version'], ascending=[True, False]).drop_duplicates('hash')
        # Merge with ref_df to get title/author
        meta_df = latest_links.merge(lib.ref_df, on='tag', how='left')
        for _, row in meta_df.iterrows():
            prefix = str(row.hash)[:10]
            hash_prefix_to_meta[prefix] = {
                'tag': row.tag,
                'title': str(row.get('title', '')).replace('{', '').replace('}', ''),
                'authors': trim_author(row.get('author', '')),
                'publisher': str(row.get('publisher', '')) if pd.notna(row.get('publisher')) else ''
            }
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
            
        if event.get('type') not in ['match', 'context']:
            continue
            
        data = event['data']
        path_text = data.get('path', {}).get('text')
        if not path_text:
            continue
            
        # Extract 10-char hash prefix from filename (e.g. 1DE582E107_1...)
        filename = Path(path_text).name
        hash_prefix = filename[:10]
        
        if hash_prefix not in blocks_by_hash_prefix:
            meta = hash_prefix_to_meta.get(hash_prefix, {})
            
            block = {
                'hash': hash_prefix, # Use prefix for display if full hash unknown
                'tag': meta.get('tag'),
                'title': meta.get('title', ''),
                'authors': meta.get('authors', ''),
                'lines': []
            }
            blocks_by_hash_prefix[hash_prefix] = block
            blocks.append(block)
            
        # Format line with highlights
        line_text = data.get('lines', {}).get('text', '')
        line_number = data.get('line_number')
        submatches = data.get('submatches', [])
        
        # Escape HTML and then inject <mark> tags
        formatted_line = ""
        last_pos = 0
        
        for match in sorted(submatches, key=lambda x: x['start']):
            start = match['start']
            end = match['end']
            formatted_line += html.escape(line_text[last_pos:start])
            formatted_line += f'<mark>{html.escape(line_text[start:end])}</mark>'
            last_pos = end
            
        formatted_line += html.escape(line_text[last_pos:])
        
        blocks_by_hash_prefix[hash_prefix]['lines'].append({
            'type': event['type'],
            'number': line_number,
            'text': formatted_line
        })
        
    return blocks

@bp.route('/author-search/<path:author>')
def author_search(author):
    lib = LibraryContext.get()
    if lib.is_empty: return "No library"

    view_mode = request.args.get('view_mode', 'list')
    
    try:
        header_oob, results_html = render_author_results_parts(lib, author, view_mode, oob_header=True)
        return header_oob + results_html
        
    except Exception as e:
        logger.error(f"Author search error: {e}")
        return f"Error: {str(e)}"

@bp.route('/search')
def search():
    raw_query = request.args.get('q', '').strip()
    
    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."
    
    if not raw_query:
        return _render_query_feedback(QUERY_HELP_TEXT, state="muted")

    spec = normalize_query(
        raw_query,
        default_limit=50,
        recent=True,
    )

    if not spec.query:
        return _render_query_feedback(QUERY_HELP_TEXT, state="muted")

    try:
        df = lib.database
        result = df.querex(spec.expression)
        
        if not isinstance(result, pd.DataFrame):
            return _render_query_feedback(
                f"{spec.expression} (query error: result is {type(result).__name__})",
                state="error",
            )

        view_mode = request.args.get('view_mode', 'list')
        return _render_query_feedback(spec.expression, state="ok") + render_search_results(result, view_mode=view_mode)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return _render_query_feedback(_query_error_message(spec.expression, e), state="error")


@bp.route('/search-export-csv')
def search_export_csv():
    raw_query = request.args.get('q', '').strip()
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query:
        return "No query provided.", 400

    try:
        spec = normalize_query(raw_query, default_limit=50, recent=True)

        df = lib.database
        export_df = df.querex(spec.expression)

        if not isinstance(export_df, pd.DataFrame) or export_df.empty:
            return "No matches found to export.", 400

        return export_dataframe_to_csv(export_df, query_export_filename(raw_query))
    except Exception as e:
        logger.error(f"Search export error: {e}")
        return str(e), 500


@bp.route('/search-export-bibtex')
def search_export_bibtex():
    raw_query = request.args.get('q', '').strip()
    plus = request.args.get('plus') == '1'
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query:
        return "No query provided.", 400

    try:
        spec = normalize_query(raw_query, default_limit=50, recent=True)
        export_df = lib.database.querex(spec.expression)

        if not isinstance(export_df, pd.DataFrame) or export_df.empty:
            return "No matches found to export.", 400

        return export_dataframe_to_bibtex(export_df, lib, plus=plus)
    except Exception as e:
        logger.error(f"Search BibTeX export error: {e}")
        return str(e), 500


def _render_query_feedback(message: str, *, state: str = "ok") -> str:
    classes = {
        "ok": "query-feedback is-ok",
        "error": "query-feedback is-error",
        "muted": "query-feedback",
    }.get(state, "query-feedback")
    return render_template(
        "components/query_feedback.html",
        message=message,
        classes=classes,
    )


def _query_error_message(expression: str, error: Exception) -> str:
    raw = str(error).replace("\n", " ").strip()
    lowered = raw.lower()
    if "unexpected end-of-input" in lowered:
        return f"{expression} (incomplete query)"
    if "failed to parse query:" in lowered:
        marker = "Error:"
        if marker in raw:
            raw = raw.split(marker, 1)[1].strip()
    return f"{expression} (syntax error: {raw})"
