from .shared import *

@bp.route('/authors')
def authors_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    def calc_authors():
        if lib.ref_df.empty: return []
        exploded = lib.ref_df[['tag', 'author']].copy()
        exploded['author'] = exploded['author'].str.split(' and ')
        exploded = exploded.explode('author')
        author_counts = exploded['author'].value_counts()
        return [(name, count) for name, count in author_counts.items() if name and name.strip()]
    
    authors = get_cached_data(lib, 'author_counts', calc_authors)
    selected_author = request.args.get('author', '')
    author_results_header_html = ''
    author_results_html = ''
    if selected_author:
        try:
            author_results_header_html, author_results_html = render_author_results_parts(
                lib,
                selected_author,
                request.args.get('view_mode', 'list'),
                oob_header=False,
            )
        except Exception as e:
            logger.error(f"Author preload error: {e}")
            author_results_html = f"Error: {str(e)}"
    
    return render_template(
        'authors.html',
        lib=lib,
        authors=authors,
        selected_author=selected_author,
        author_results_header_html=author_results_header_html,
        author_results_html=author_results_html,
    )

@bp.route('/insights')
def insights_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    def calc_insights():
        total_refs = len(lib.ref_df)
        total_docs = len(lib.doc_df)
        
        # Orphans (files with no tags)
        tagged_hashes = set(lib.ref_doc_df.hash.unique())
        total_orphans = len(lib.doc_df[~lib.doc_df.hash.isin(tagged_hashes)])
        
        # 2. Top Authors (need to explode first)
        top_authors = []
        if not lib.ref_df.empty and 'author' in lib.ref_df.columns:
            exploded = lib.ref_df.assign(author=lib.ref_df.author.str.split(' and ')).explode('author')
            top_authors = exploded['author'].value_counts().head(10).to_dict().items()
            
        # 3. Top Journals
        top_journals = []
        if not lib.ref_df.empty:
            sources = pd.concat([lib.ref_df.get('journal', pd.Series()), lib.ref_df.get('booktitle', pd.Series())])
            top_journals = sources[sources != ""].value_counts().head(10).to_dict().items()
            
        # 4. Top Years
        top_years = []
        if not lib.ref_df.empty and 'year' in lib.ref_df.columns:
            top_years = lib.ref_df['year'].value_counts().head(10).to_dict().items()
            top_years = sorted(top_years, key=lambda x: str(x[0]), reverse=True)
            
        # 5. Top Publishers
        top_publishers = []
        if not lib.ref_df.empty and 'publisher' in lib.ref_df.columns:
            top_publishers = lib.ref_df['publisher'][lib.ref_df.publisher != ""].value_counts().head(10).to_dict().items()

        return {
            'total_refs': total_refs,
            'total_docs': total_docs,
            'total_orphans': total_orphans,
            'top_authors': top_authors,
            'top_journals': top_journals,
            'top_years': top_years,
            'top_publishers': top_publishers
        }

    insights = get_cached_data(lib, 'insights', calc_insights)

    # 6. Library History
    def calc_history():
        try:
            history_df = lib.history()
            if not history_df.empty:
                return history_df.reset_index().to_dict('records')
        except Exception as e:
            logger.warning(f"Could not load library history: {e}")
        return []

    history = get_cached_data(lib, 'history', calc_history)

    return render_template('insights.html', 
                           lib=lib,
                           **insights,
                           history=history)


@bp.route('/cloud')
def cloud_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    # 1. Stop words (simple list for research)
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'of', 'for', 'in', 'on', 'with', 'by', 'to', 'from',
        'is', 'are', 'was', 'were', 'that', 'this', 'those', 'these', 'it', 'its', 'their',
        'as', 'at', 'into', 'using', 'based', 'based on', 'towards', 'through', 'between',
        'during', 'each', 'every', 'other', 'some', 'any', 'all', 'such', 'very', 'not',
        'than', 'more', 'about', 'under', 'over', 'between', 'can', 'will', 'should',
        'method', 'model', 'approach', 'analysis', 'results', 'data', 'study', 'system',
        'research', 'paper', 'new', 'proposed', 'using', 'use', 'via', 'from', 'an', 'a'
    }
    
    # 2. Tokenize titles
    words = []
    for title in lib.ref_df.title.dropna():
        # Remove LaTeX braces and non-alpha
        clean_title = title.replace('{', '').replace('}', '').lower()
        tokens = re.findall(r'\b[a-z]{4,}\b', clean_title) # Words with 4+ letters
        words.extend([w for w in tokens if w not in stop_words])
    
    # 3. Frequency count
    counts = Counter(words).most_common(100)
    if not counts:
        return render_template('cloud.html', lib=lib, words=[], max_weight=1)
        
    max_weight = counts[0][1]
    
    return render_template('cloud.html', lib=lib, words=counts, max_weight=max_weight)

