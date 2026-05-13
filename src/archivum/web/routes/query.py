from .shared import *

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

    # Use native querex sorting: order by -year
    # We explicitly select year to ensure it's available for sorting
    query_expr = f"select year, path, hash, type, * ! /{author}/ order by -year"
    
    try:
        df = lib.database
        result = df.querex(query_expr)
        if not isinstance(result, pd.DataFrame):
            return f"Query error: {result}"
        
        header_oob = render_template(
            'components/author_results_header.html',
            author=author,
            view_mode=view_mode,
            view_modes=[
                ('list', 'Dense List'),
                ('verbose', 'Verbose'),
                ('table', 'Table'),
            ],
        )
        
        return header_oob + _render_search_results(result, view_mode=view_mode)
        
    except Exception as e:
        logger.error(f"Author search error: {e}")
        return f"Error: {str(e)}"

def _render_search_results(df, view_mode='list'):
    """Helper to render results consistently."""
    prepared_df = prepare_search_results(df)
    return render_template('components/results.html', results=prepared_df, view_mode=view_mode)

@bp.route('/search')
def search():
    raw_query = request.args.get('q', '').strip()
    
    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."
    
    if not raw_query:
        return ""

    spec = normalize_query(
        raw_query,
        default_limit=50,
        recent=True,
        projection="type, *",
        q_projection="path, hash, type, *",
    )

    if not spec.query:
        return ""

    try:
        df = lib.database
        result = df.querex(spec.expression)
        
        if not isinstance(result, pd.DataFrame):
            return f"Query error: result is {type(result)}"

        view_mode = request.args.get('view_mode', 'list')
        return _render_search_results(result, view_mode=view_mode)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return render_template('components/alert.html', level='danger', message=f"Error: {str(e)}", classes='error')


@bp.route('/search-export-csv')
def search_export_csv():
    raw_query = request.args.get('q', '').strip()
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query:
        return "No query provided.", 400

    try:
        spec = normalize_query(raw_query, default_limit=None, recent=False, projection="*")

        df = lib.database
        export_df = df.querex(spec.expression)

        if not isinstance(export_df, pd.DataFrame) or export_df.empty:
            return "No matches found to export.", 400

        # Filename generation: arc-MM-DD-shortened-query.csv
        date_str = datetime.now().strftime("%m-%d")
        clean_q = re.sub(r'[^a-zA-Z0-9]+', '-', raw_query).strip('-')[:30]
        filename = f"arc-{date_str}-{clean_q}.csv"

        # Save to temp and send
        temp_path = Path("temp") / filename
        export_df.to_csv(temp_path, index=False, encoding='utf-8-sig') # BOM for Excel
        
        return send_file(str(temp_path.absolute()), as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Search export error: {e}")
        return str(e), 500
