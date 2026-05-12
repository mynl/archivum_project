from .shared import *

@bp.route('/network')
def network_page():
    lib = LibraryContext.get()
    return render_template('network.html', lib=lib)

@bp.route('/network-data')
def network_data():
    raw_query = request.args.get('q', '').strip()
    verbosity = request.args.get('verbosity', 'minimal')
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query: return {"nodes": [], "edges": [], "elements": [], "papers": 0, "clusters": []}

    try:
        df = lib.database
        universe_hashes = resolve_universe(lib, raw_query)
        result_df = df[df['hash'].astype(str).isin(universe_hashes)]
        
        if result_df.empty:
            return {"nodes": [], "edges": [], "elements": [], "papers": 0, "clusters": []}

        # Social Graph Logic...
        paper_to_authors = {}
        author_to_papers = {}
        
        def normalize_name(name):
            if pd.isna(name): return "Unknown"
            s = str(name).strip()
            if not s or s.lower() == 'nan' or s.lower() == 'unknown': return "Unknown"
            return s.rstrip('.').strip()

        for _, row in result_df.iterrows():
            authors_raw = row.get('author')
            if pd.isna(authors_raw): continue
            
            author_list = [normalize_name(a) for a in str(authors_raw).split(' and ') if a.strip()]
            author_list = [a for a in author_list if a != "Unknown"]
            if not author_list: continue
            
            paper_info = {
                'title': clean_latex(str(row.get('title', 'Unknown'))),
                'year': str(row.get('year', '9999')).split('.')[0],
                'tag': str(row.tag)
            }
            for auth in author_list:
                author_to_papers.setdefault(auth, []).append(paper_info)
            paper_to_authors[str(row.tag)] = (author_list, paper_info)

        nodes = []
        for auth, papers in author_to_papers.items():
            nodes.append({
                'data': {
                    'id': str(auth), 'label': str(auth), 'weight': int(len(papers)),
                    'papers': papers[:50] 
                }
            })

        edges = {}
        for tag, (author_list, paper_info) in paper_to_authors.items():
            if len(author_list) < 2: continue
            import itertools
            for a1, a2 in itertools.combinations(sorted(author_list), 2):
                key = (str(a1), str(a2))
                if key not in edges: edges[key] = {'weight': 0, 'papers': []}
                edges[key]['weight'] += 1
                edges[key]['papers'].append(paper_info)

        elements = nodes + [
            {'data': {'source': k[0], 'target': k[1], 'weight': int(v['weight']), 'papers': v['papers']}}
            for k, v in edges.items()
        ]

        hash_list = result_df['hash'].dropna().astype(str).str[:8].unique()[:500]
        return {
            "nodes": nodes, "elements": elements, "papers": len(result_df),
            "hashes": "|".join([str(h) for h in hash_list]),
            "status_msg": f'<i class="bi bi-people me-2"></i> Social graph built for {len(result_df)} papers.',
            "clusters": []
        }
    except Exception as e:
        logger.error(f"Network error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500

@bp.route('/semantic-data')
def semantic_data():
    raw_query = request.args.get('q', '').strip()
    source_type = request.args.get('source', 'title')
    verbosity = request.args.get('verbosity', 'minimal')
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query: return {"elements": [], "papers": 0, "clusters": []}

    try:
        result = analyze_semantic(lib, raw_query, source_type)
        if result.result_df.empty:
            return {"elements": [], "papers": 0, "clusters": []}
        if result.relevant_idx.empty:
            return {
                "elements": [],
                "papers": 0,
                "omitted_count": len(result.omitted_hashes),
                "omitted_reason": "No text extracts found.",
                "clusters": [],
            }
        return result.to_cytoscape_json(verbosity=verbosity)
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

        # Filename
        date_str = datetime.now().strftime("%m-%d")
        clean_q = re.sub(r'[^a-zA-Z0-9]+', '-', raw_query).strip('-')[:30]
        filename = f"arc-galaxy-{date_str}-{clean_q}.csv"

        temp_path = Path("temp") / filename
        export_df.to_csv(temp_path, index=False, encoding='utf-8-sig')
        
        return send_file(str(temp_path.absolute()), as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Semantic export error: {e}")
        return str(e), 500
