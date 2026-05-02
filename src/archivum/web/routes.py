from flask import Blueprint, render_template, request, send_file, abort
from ..cli import LibraryContext
import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    return render_template('query.html')

@bp.route('/search')
def search():
    raw_query = request.args.get('q', '').strip()
    
    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."
    
    if not raw_query:
        return ""

    # Determine search type and actual query string
    if raw_query.startswith('q '):
        search_type = 'q'
        query = raw_query[2:].strip()
    elif raw_query.startswith('f '):
        search_type = 'f'
        query = raw_query[2:].strip()
    else:
        # Default to fuzzy
        search_type = 'f'
        query = raw_query

    if not query:
        return ""

    try:
        if search_type == 'f':
            # Fuzzy search
            if query[0] != "!" and query.find("~") == -1:
                query_expr = f"recent top 50 tag ~ {query}"
            else:
                query_expr = query
                if "top" not in query_expr.lower():
                    query_expr = "top 50 " + query_expr
                if "recent" not in query_expr.lower():
                    query_expr = "recent " + query_expr
        else:
            # Querex search
            query_expr = query
            if "select" not in query_expr.lower():
                query_expr = "select path, hash, type, * " + query_expr

        # Perform the query
        df = lib.ref_df
        result = df.querex(query_expr)
        
        if not isinstance(result, pd.DataFrame):
            return f"Query error: result is {type(result)}"

        # 1. Clean up LaTeX braces and handle NaN in display copy
        display_results = result.copy()
        
        # Helper to find column regardless of case
        def find_col(df, target):
            cols = {c.lower(): c for c in df.columns}
            return cols.get(target.lower())

        type_col = find_col(display_results, 'type') or find_col(display_results, 'entrytype')
        year_col = find_col(display_results, 'year')
        title_col = find_col(display_results, 'title')
        author_col = find_col(display_results, 'author')

        def clean_latex(s):
            if not isinstance(s, str):
                return ""
            return s.replace('{', '').replace('}', '')

        def trim_author(s):
            if not isinstance(s, str) or not s:
                return ""
            # Split and clean
            author_bits = [i.split(",")[0].strip("{} ") for i in s.split(" and ")]
            if len(author_bits) > 1:
                *first, last = author_bits
                name = ", ".join(first) + f", and {last}"
            else:
                name = author_bits[0] if author_bits else ""
            return name.replace('{', '').replace('}', '')

        # Clean strings
        for col in [title_col, author_col, find_col(display_results, 'journal'), find_col(display_results, 'publisher')]:
            if col:
                display_results[col] = display_results[col].apply(clean_latex)
        
        if author_col:
            display_results['author_display'] = display_results[author_col].apply(trim_author)
        else:
            display_results['author_display'] = ""

        # Mark books (case-insensitive)
        if type_col:
            display_results['is_book'] = display_results[type_col].fillna('').astype(str).str.lower().str.contains('book')
        else:
            display_results['is_book'] = False

        # Ensure year is a string and handle NaNs
        if year_col:
            display_results['year_display'] = display_results[year_col].fillna('').astype(str).apply(lambda x: x.split('.')[0] if '.' in x else x)
        else:
            display_results['year_display'] = ""
            
        # Ensure title is mapped for template
        if title_col:
            display_results['title_display'] = display_results[title_col]
        else:
            display_results['title_display'] = "[No Title]"

        view_mode = request.args.get('view_mode', 'list')
        return render_template('components/results.html', results=display_results, view_mode=view_mode)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"<div class='error'>Error: {str(e)}</div>"

def get_path_for_tag(lib, tag):
    """Resolve a tag to a physical file path."""
    if lib.is_empty:
        return None
    
    # 1. Find the hashes associated with this tag in ref_doc_df
    doc_links = lib.ref_doc_df[lib.ref_doc_df.tag == tag]
    if doc_links.empty:
        return None
    
    # 2. Join with doc_df to get the physical path
    doc_details = doc_links.merge(lib.doc_df, on=['hash', 'version'], how='inner')
    if doc_details.empty:
        return None
    
    # 3. Return the absolute path of the first matching document
    # Using lib.abspath if it exists, otherwise just the path
    rel_path = doc_details.iloc[0].path
    if hasattr(lib, 'abspath'):
        return lib.abspath(rel_path)
    return rel_path

@bp.route('/view/<tag>')
def view(tag):
    lib = LibraryContext.get()
    path = get_path_for_tag(lib, tag)
    
    if not path or not Path(path).exists():
        logger.error(f"File not found for tag {tag} at path {path}")
        abort(404, description="File not found")
    
    try:
        return send_file(path, mimetype='application/pdf', as_attachment=False)
    except Exception as e:
        logger.error(f"View error: {e}")
        abort(500)

@bp.route('/status')
def status():
    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."
    
    # Gather status info similar to cli 'status' command
    status_info = {
        "name": lib.name,
        "config_path": str(lib.config_path),
        "doc_store": str(lib.doc_store_path),
        "ref_count": len(lib.ref_df),
        "doc_count": len(lib.doc_df),
        "ref_doc_count": len(lib.ref_doc_df),
    }
    return render_template('status.html', status=status_info)

@bp.route('/history')
def history():
    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."
    
    hist_df = lib.history()
    return render_template('history.html', history=hist_df)

@bp.route('/sync-check')
def sync_check():
    lib = LibraryContext.get()
    if lib.needs_reload:
        # In a real app we might trigger a reload here or just inform the user
        # For now, just return status
        return "reload-needed", 200
    return "ok", 200
