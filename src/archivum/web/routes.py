from flask import Blueprint, render_template, request, send_file, abort, Response, stream_with_context
from ..cli import LibraryContext
from ..utilities import trim_author, clean_latex
import pandas as pd
from pathlib import Path
import logging
import json
import html
import subprocess
import os
import time
import re
from datetime import datetime
from collections import Counter

logger = logging.getLogger(__name__)

# Simple in-memory cache for expensive library analytics
_CACHE = {
    'lib_id': None,
    'last_sync': None,
    'author_counts': None,
    'history': None,
    'insights': None,
    'audit': None
}

def get_cached_data(lib, key, calculator):
    """Retrieve from cache or calculate if library state changed."""
    global _CACHE
    lib_id = id(lib)
    # Use disk mtime of the feather files as a more robust change indicator
    feather_path = lib.config_path / "ref.feather"
    last_sync = feather_path.stat().st_mtime if feather_path.exists() else 0
    
    # Invalidate if it's a different library instance or if ref_df might have changed
    if _CACHE['lib_id'] != lib_id or _CACHE['last_sync'] != last_sync:
        logger.info(f"Invalidating cache for {lib.name} (last_sync: {last_sync})")
        _CACHE = {
            'lib_id': lib_id,
            'last_sync': last_sync,
            'author_counts': None,
            'history': None,
            'insights': None,
            'audit': None
        }
    
    if _CACHE.get(key) is None:
        start_time = time.time()
        _CACHE[key] = calculator()
        logger.info(f"Calculated {key} in {time.time() - start_time:.2f}s")
    
    return _CACHE[key]

bp = Blueprint('main', __name__)

@bp.route('/health')
def health_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    action = request.args.get('action')
    if action == 'clean_extracts':
        lib.clean_text_extracts(execute=True)
        # Manually invalidate cache after destructive action
        global _CACHE
        _CACHE['audit'] = None
    
    def calc_audit():
        return lib.audit()
        
    findings = get_cached_data(lib, 'audit', calc_audit)
    return render_template('health.html', lib=lib, findings=findings)

@bp.route('/ingest')
def ingest_page():
    lib = LibraryContext.get()
    return render_template('ingest.html', lib=lib)

@bp.route('/ingest/start', methods=['POST'])
def ingest_start():
    lib = LibraryContext.get()
    if lib.is_empty: abort(400, "No library open")

    bibtex = request.form.get('bibtex', '')
    url_path = request.form.get('url_path', '')
    uploaded_file = request.files.get('file')

    temp_dir = Path("temp/staging")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    temp_path = None
    temp_filename = None

    if uploaded_file and uploaded_file.filename:
        temp_path = temp_dir / uploaded_file.filename
        uploaded_file.save(temp_path)
        temp_filename = uploaded_file.filename
    elif url_path:
        p = Path(url_path)
        if p.exists() and p.is_file():
            temp_path = p
            temp_filename = p.name
        elif url_path.startswith(('http://', 'https://')):
            # Simple download
            import requests
            
            # Robust headers to avoid anti-bot blocks (e.g. from Arxiv)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            # Arxiv specific: if it's an /abs/ link, convert to /pdf/
            if 'arxiv.org/abs/' in url_path:
                url_path = url_path.replace('/abs/', '/pdf/')
                if not url_path.endswith('.pdf'): url_path += '.pdf'

            try:
                r = requests.get(url_path, stream=True, headers=headers, timeout=30)
                r.raise_for_status()
                
                # Check content type
                content_type = r.headers.get('Content-Type', '').lower()
                if 'html' in content_type:
                    return f"Error: URL returned a web page (HTML) instead of a PDF. Please provide a direct link to the PDF.", 400

                filename = url_path.split('/')[-1].split('?')[0] or "downloaded.pdf"
                if not filename.endswith(('.pdf', '.djvu')): filename += ".pdf"
                
                temp_path = temp_dir / filename
                with open(temp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                temp_filename = filename
            except Exception as e:
                return f"Error downloading URL: {str(e)}", 400

    if not temp_path:
        return "No file or URL provided", 400

    # Process document
    from ..document import Document
    from ..hasher import hash_many3
    
    doc = Document(temp_path)
    hashes = hash_many3([temp_path], workers=lib.config.hash_workers)
    doc.hash = hashes.get(temp_path, "")
    
    # Check for duplicates
    duplicate_tag = None
    if doc.hash:
        lib_docs = lib.doc_df
        if doc.hash in lib_docs.hash.values:
            match_tags = lib.ref_doc_df[lib.ref_doc_df.hash == doc.hash].tag.tolist()
            duplicate_tag = match_tags[0] if match_tags else "Unknown"

    # Discovery
    doc.process()
    
    # If no bibtex provided, generate from discovery
    if not bibtex:
        from ..bibtex import dict_to_bibtex
        data = doc.bib.copy()
        # Ensure authors are sorted Last, First
        if data.get('author'):
            data['author'] = Document._sort_authors(data['author'])
        data['tag'] = doc.key()
        bibtex = dict_to_bibtex(data)
    else:
        # If bibtex was pasted, also try to normalize the authors in it
        from ..bibtex import bibtex_to_dict, dict_to_bibtex
        try:
            entries = bibtex_to_dict(bibtex)
            if entries:
                tag, data = list(entries.items())[0]
                if data.get('author'):
                    data['author'] = Document._sort_authors(data['author'])
                data['tag'] = tag
                bibtex = dict_to_bibtex(data)
        except Exception:
            pass # Keep original if parsing fails

    return render_template('_ingest_workbench.html', 
                           lib=lib, 
                           doc=doc, 
                           bibtex=bibtex, 
                           temp_path=str(temp_path),
                           temp_filename=temp_filename,
                           duplicate_tag=duplicate_tag)

@bp.route('/ingest/enhance', methods=['POST'])
def ingest_enhance():
    lib = LibraryContext.get()
    action = request.args.get('action')
    bibtex = request.form.get('bibtex', '')

    from ..bibtex import bibtex_to_dict, dict_to_bibtex
    try:
        # Simple parser for single entry
        entries = bibtex_to_dict(bibtex)
        if not entries: return bibtex
        tag, data = list(entries.items())[0]
        data['tag'] = tag
        
        if action == 'names':
            authors = data.get('author', '')
            if authors:
                from ..document import Document
                author_list = authors.split(' and ')
                new_authors = []
                for a in author_list:
                    # Extend using Trie
                    extended = lib.to_name_ex(a.strip())
                    # Ensure Last, First
                    new_authors.append(Document._sort_authors(extended))
                data['author'] = ' and '.join(new_authors)
            bibtex = dict_to_bibtex(data)
            
        elif action == 'tag':
            from ..utilities import TagAllocator
            ta = TagAllocator(set(lib.ref_df.tag.tolist()))
            
            author = data.get('author', 'Unknown')
            year = data.get('year', '9999')
            
            # Extract first author last name
            first_author = author.split(' and ')[0]
            if ',' in first_author:
                last_name = first_author.split(',')[0].strip()
            else:
                last_name = first_author.split(' ')[-1].strip()
            
            last_name = "".join(c for c in last_name if c.isalnum())
            new_tag = ta.get_tag(last_name, year)
            data['tag'] = new_tag
            bibtex = dict_to_bibtex(data)
            
    except Exception as e:
        logger.error(f"Enhance error: {e}")
        return bibtex

    return bibtex

@bp.route('/ingest/commit', methods=['POST'])
def ingest_commit():
    lib = LibraryContext.get()
    bibtex = request.form.get('bibtex')
    temp_path = Path(request.form.get('temp_path'))
    h = request.form.get('hash')

    from ..bibtex import bibtex_to_dict
    from ..library import Library
    
    try:
        entries = bibtex_to_dict(bibtex)
        if not entries: return "Invalid BibTeX", 400
        tag, data = list(entries.items())[0]

        # Final check for tag uniqueness
        if tag in lib.ref_df.tag.values:
            return f"Error: Tag '{tag}' already exists.", 400

        # Import using the library's internal logic
        # We'll simulate an importer object to use lib.update()
        class MockImporter:
            def __init__(self, tag, data, h, path, lib):
                import pandas as pd
                from ..enhancements import canonical_name_from_row
                import os
                
                # We need a row-like object for canonical_name_from_row
                class Row:
                    def __init__(self, tag, data, h, path):
                        self.tag = tag
                        self.hash = h
                        self.author = data.get('author', 'Unknown')
                        self.title = data.get('title', 'Unknown')
                        self.year = data.get('year', '9999')
                        self.path = path.name
                
                row = Row(tag, data, h, path)
                fn = canonical_name_from_row(row)
                full_fn = f"{fn}{path.suffix}"
                dest_path = lib.doc_store_path / fn[:2].upper() / full_fn
                
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if not dest_path.exists():
                    import shutil
                    shutil.copy2(path, dest_path)
                
                rel_path = os.path.relpath(dest_path, lib.doc_store_path)
                
                # Metadata compliance
                st = os.stat(dest_path)
                
                # Precise Timestamps with Timezone (using current system timezone)
                # Archivum usually uses local timezone for these.
                def to_precise_ts(t):
                    return pd.Timestamp(t, unit='s', tz='Europe/London').floor('ns')

                self.ref_df = pd.DataFrame([data])
                self.ref_df['tag'] = tag
                
                self.doc_df = pd.DataFrame([{
                    "name": full_fn,
                    "path": rel_path.replace("\\", "/"),
                    "mod": to_precise_ts(st.st_mtime),
                    "create": to_precise_ts(getattr(st, 'st_ctime', st.st_mtime)),
                    "access": to_precise_ts(st.st_atime),
                    "node": st.st_ino,
                    "links": 1,
                    "size": st.st_size,
                    "suffix": path.suffix,
                    "hash": h,
                    "version": 0
                }])
                
                self.ref_doc_df = pd.DataFrame([{
                    "tag": tag,
                    "hash": h,
                    "version": 0
                }])

        importer = MockImporter(tag, data, h, temp_path, lib)
        lib.update(importer)
        
        # Add to import-audit history to maintain CLI compatibility
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            audit_dir = lib.config_path / "import-audit" / timestamp
            audit_dir.mkdir(parents=True, exist_ok=True)
            
            import_info_dict = {
                "created": datetime.now().isoformat(),
                "bibtex_file": "web-ingest",
                "raw_entries": 1,
                "net_entries": 1
            }
            import_info = pd.DataFrame(import_info_dict.items(), columns=["key", "value"])
            import_info.to_csv(audit_dir / f"{timestamp}.audit-info.csv")
            
            # Also save tag mapping for completeness
            tag_map = pd.DataFrame([{"old_tag": "web", "new_tag": tag}])
            tag_map.to_csv(audit_dir / f"{timestamp}.tag-mapping.csv")
        except Exception as audit_err:
            logger.warning(f"Failed to write audit log: {audit_err}")

        # Trigger full-text extraction in background
        from ..document import extract_text_for_paths
        fn = importer.doc_df.iloc[0].path
        extract_text_for_paths([lib.doc_store_path / fn], 
                               text_dir_path=lib.text_dir_path,
                               extractor=lib.config.extractor)

        return f'<div class="alert alert-success mt-4">Successfully archived <strong>{tag}</strong>! <a href="/view/{tag}" target="_blank">View PDF</a></div>'
    
    except Exception as e:
        logger.error(f"Commit error: {e}")
        return f'<div class="alert alert-danger mt-4">Error: {str(e)}</div>'

@bp.route('/view-temp/<filename>')
def view_temp(filename):
    temp_path = Path("temp/staging") / filename
    if not temp_path.exists(): abort(404)
    return send_file(str(temp_path.absolute()), mimetype='application/pdf')

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
    
    return render_template('authors.html', lib=lib, authors=authors, selected_author=selected_author)

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
                'authors': trim_author(row.get('author', ''))
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

    # Use native querex sorting: order by -year
    # We explicitly select year to ensure it's available for sorting
    query_expr = f"select year, path, hash, type, * ! /{author}/ order by -year"
    
    try:
        df = lib.database
        result = df.querex(query_expr)
        if not isinstance(result, pd.DataFrame):
            return f"Query error: {result}"
        
        return _render_search_results(result, view_mode='list')
        
    except Exception as e:
        logger.error(f"Author search error: {e}")
        return f"Error: {str(e)}"

def _prepare_search_results(df):
    """Clean and format a DataFrame for display in search results."""
    if not isinstance(df, pd.DataFrame):
        return df
    
    display_results = df.copy()
    
    def find_col(df, target):
        cols = {c.lower(): c for c in df.columns}
        return cols.get(target.lower())

    type_col = find_col(display_results, 'type')
    year_col = find_col(display_results, 'year')
    title_col = find_col(display_results, 'title')
    author_col = find_col(display_results, 'author')
    journal_col = find_col(display_results, 'journal')
    publisher_col = find_col(display_results, 'publisher')

    # Clean strings
    for col in [title_col, author_col, journal_col, publisher_col]:
        if col:
            display_results[col] = display_results[col].apply(clean_latex)
    
    if author_col:
        display_results['author_display'] = display_results[author_col].apply(trim_author)
    else:
        display_results['author_display'] = ""

    if type_col:
        display_results['is_book'] = display_results[type_col].fillna('').astype(str).str.lower().isin(['book', '@book'])
    else:
        display_results['is_book'] = False

    if year_col:
        display_results['year_display'] = display_results[year_col].fillna('').astype(str).apply(lambda x: x.split('.')[0] if '.' in x else x)
    else:
        display_results['year_display'] = ""
        
    if title_col:
        display_results['title_display'] = display_results[title_col]
    else:
        display_results['title_display'] = "[No Title]"

    return display_results

def _render_search_results(df, view_mode='list'):
    """Helper to render results consistently."""
    prepared_df = _prepare_search_results(df)
    return render_template('components/results.html', results=prepared_df, view_mode=view_mode)

@bp.route('/search')
def search():
    raw_query = request.args.get('q', '').strip()
    
    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."
    
    if not raw_query:
        return ""

    # Determine search type and actual query string
    lower_query = raw_query.lower()
    if lower_query.startswith('q '):
        search_type = 'q'
        query = raw_query[2:].strip()
    elif lower_query.startswith('f '):
        search_type = 'f'
        query = raw_query[2:].strip()
    else:
        search_type = 'f'
        query = raw_query

    if not query:
        return ""

    try:
        if search_type == 'f':
            if query[0] != "!" and query.find("~") == -1:
                query_expr = f"recent top 50 select type, * tag ~ {query}"
            else:
                query_expr = query
                if "select" not in query_expr.lower():
                    query_expr = "select type, * " + query_expr
                if "top" not in query_expr.lower():
                    query_expr = "top 50 " + query_expr
                if "recent" not in query_expr.lower():
                    query_expr = "recent " + query_expr
        else:
            query_expr = query
            if "select" not in query_expr.lower():
                query_expr = "select path, hash, type, * " + query_expr

        df = lib.database
        result = df.querex(query_expr)
        
        if not isinstance(result, pd.DataFrame):
            return f"Query error: result is {type(result)}"

        view_mode = request.args.get('view_mode', 'list')
        return _render_search_results(result, view_mode=view_mode)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"<div class='error'>Error: {str(e)}</div>"

@bp.route('/ripgrep')
def ripgrep_page():
    return render_template('ripgrep.html')

# Global metadata cache to avoid rebuilding on every request
_META_CACHE_GLOBAL = {
    'lib_name': None,
    'mtime': 0,
    'data': {}
}

def get_hash_meta_cache(lib):
    global _META_CACHE_GLOBAL
    feather_path = lib.config_path / "ref.feather"
    mtime = feather_path.stat().st_mtime if feather_path.exists() else 0

    if _META_CACHE_GLOBAL['lib_name'] != lib.name or _META_CACHE_GLOBAL['mtime'] != mtime:
        logger.info(f"Rebuilding metadata cache for {lib.name}...")
        hash_prefix_to_meta = {}
        if not lib.ref_doc_df.empty:
            # Sort by version to get latest, then drop duplicate hashes
            latest_links = lib.ref_doc_df.sort_values(['hash', 'version'], ascending=[True, False]).drop_duplicates('hash')
            meta_df = latest_links.merge(lib.ref_df, on='tag', how='left')
            for _, row in meta_df.iterrows():
                prefix = str(row.hash)[:10]
                hash_prefix_to_meta[prefix] = {
                    'tag': row.tag,
                    'title': str(row.get('title', '')).replace('{', '').replace('}', ''),
                    'authors': trim_author(row.get('author', ''))
                }
        _META_CACHE_GLOBAL['lib_name'] = lib.name
        _META_CACHE_GLOBAL['mtime'] = mtime
        _META_CACHE_GLOBAL['data'] = hash_prefix_to_meta

    return _META_CACHE_GLOBAL['data']

@bp.route('/rg-search')
def rg_search():
    query = request.args.get('q', '').strip()
    show_files = request.args.get('files') == 'true'

    if not query and not show_files:
        return ""

    context_a = request.args.get('after', '0')
    context_b = request.args.get('before', '0')
    show_counts = request.args.get('counts') == 'true'
    case_sensitive = request.args.get('case') == 'sensitive'
    glob1 = request.args.get('glob1', '').strip()
    glob2 = request.args.get('glob2', '').strip()
    filter_mode = request.args.get('filter', 'tagged')

    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."

    # Use global cache
    hash_prefix_to_meta = get_hash_meta_cache(lib)
    total_docs_in_lib = len(lib.doc_df)

    def generate_results():
        start_time = time.time()
        logger.info(f"Starting rg_search generator for query: {query}, filter: {filter_mode}")

        # 1. Reset UI areas immediately
        yield f"<div id='rg-results' hx-swap-oob='innerHTML'></div>"
        yield f"<div id='rg-stats-header' hx-swap-oob='innerHTML'></div>"
        yield f"<div id='rg-more-container' hx-swap-oob='innerHTML' style='display: none;'></div>"

        def format_glob(g):
            if not g: return None
            if '*' in g or '?' in g or '[' in g: return g
            if '.' in g: return f'*{g}*'
            return f'*{g}*.md'

        # 2. Build common arguments
        common_args = []
        g1 = format_glob(glob1)
        if g1: common_args.extend(['--iglob', g1])
        g2 = format_glob(glob2)
        if g2: common_args.extend(['--iglob', g2])
        if not (g1 or g2): common_args.extend(['-g', '*.md'])

        # 3. Handle File-only search
        if show_files:
            cmd = ['rg', '--files'] + common_args + [str(lib.text_dir_path)]
            rg_cmd = f"{' '.join(cmd)}"
            yield f"<div id='rg-status' class='rg-info' style='margin-bottom: 1rem; display: block;' hx-swap-oob='true'>Last Command: <code>{html.escape(rg_cmd)}</code></div>"

            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                files = []
                for line in proc.stdout:
                    p = line.strip()
                    if not p: continue
                    h_prefix = Path(p).name[:10]
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    if filter_mode == 'tagged' and not meta.get('tag'): continue
                    files.append({'hash': h_prefix, 'tag': meta.get('tag'), 'title': meta.get('title', h_prefix)})

                yield f"<div hx-swap-oob='innerHTML:#rg-results'>{render_template('components/rg_files.html', files=files)}</div>"

                elapsed = time.time() - start_time
                stats_html = f"<div class='text-muted small'>Searched ~<b>{total_docs_in_lib}</b> docs; found <b>{len(files)}</b> matches in {elapsed:.3f}s</div>"
                yield f"<div id='rg-stats-header' hx-swap-oob='innerHTML'>{stats_html}</div>"
            except Exception as e:
                yield f"<div class='error' hx-swap-oob='beforeend:#rg-results'>Ripgrep Error: {str(e)}</div>"
            return

        # 4. Normal search or counts
        args = ["-n", "-H"] # Line numbers and filename
        args.extend(common_args)
        if not case_sensitive: args.append('-i')
        if not show_counts: args.extend(['-A', context_a, '-B', context_b])
        
        # We need the underlying proc to stream output
        rc, proc = lib.run_ripgrep(query, args)
        full_cmd = ["rg", "--stats", "-C", "1", "--encoding", "utf-8"] + args + [f'"{query}"', lib.text_dir_full_name]
        yield f"<div id='rg-status' class='rg-info' style='margin-bottom: 1rem; display: block;' hx-swap-oob='true'>Last Command: <code>{html.escape(" ".join(full_cmd))}</code></div>"

        # 5. Handle Streaming Search / Counts
        limit = 500
        rendered_matches = 0 # Matches actually sent to browser
        total_matches = 0    # All matches found by RG
        seen_hashes = set()
        rg_internal_time = "0.000s"
        current_block = None
        last_file = None

        def parse_stats(stats_text):
            # Ripgrep reports CPU time as 'X.X seconds spent searching' 
            # and wall-clock time as just 'X.X seconds' on the next line.
            m = re.findall(r'(\d+\.\d+) seconds', stats_text)
            if m:
                # The last match in the stats block is the wall-clock time
                return f"{float(m[-1]):.3f}s"
            return "0.000s"

        stats_buffer = []
        is_stats_section = False

        if show_counts:
            counts = {}
            for line in proc.stdout:
                if not line.strip(): continue
                # Trigger stats on typical RG summary line
                if re.match(r'^\s*\d+ matches', line) or re.match(r'^\s*\d+ matched lines', line):
                    is_stats_section = True
                
                if is_stats_section:
                    stats_buffer.append(line)
                    continue

                # Expected: .\00\hash.md:line:text
                parts = line.split(':', 2)
                if len(parts) < 3: continue
                
                clean_path = parts[0].replace('.\\', '').replace('./', '')
                h_prefix = Path(clean_path).name[:10]
                
                # Verify it passes the filter
                meta = hash_prefix_to_meta.get(h_prefix, {})
                if filter_mode == 'tagged' and not meta.get('tag'): continue
                
                counts[h_prefix] = counts.get(h_prefix, 0) + 1
            
            rg_internal_time = parse_stats("".join(stats_buffer))
            counts_list = []
            for h_prefix, count_val in counts.items():
                meta = hash_prefix_to_meta.get(h_prefix, {})
                counts_list.append({'hash': h_prefix, 'count': count_val, 'tag': meta.get('tag'), 'title': meta.get('title', ''), 'authors': meta.get('authors', '')})
            counts_list.sort(key=lambda x: x['count'], reverse=True)
            yield f"<div hx-swap-oob='innerHTML:#rg-results'>{render_template('components/rg_counts.html', counts=counts_list)}</div>"
            
            total_elapsed = time.time() - start_time
            stats_html = f"<div class='text-muted small'>Searched ~<b>{total_docs_in_lib}</b> docs; summarized <b>{len(counts_list)}</b> documents in {total_elapsed:.3f}s (RG: {rg_internal_time})</div>"
            yield f"<div id='rg-stats-header' hx-swap-oob='innerHTML'>{stats_html}</div>"
            return

        # 6. Normal Streaming with 500-match limit
        for line in proc.stdout:
            if not line.strip(): continue
            if re.match(r'^\s*\d+ matches', line) or re.match(r'^\s*\d+ matched lines', line):
                is_stats_section = True
            if is_stats_section:
                stats_buffer.append(line)
                continue

            # Text format: filename:line:text or filename-line-text
            is_match = ':' in line
            is_context = '-' in line and not is_match
            if not (is_match or is_context): continue
            
            sep = ':' if is_match else '-'
            parts = line.split(sep, 2)
            if len(parts) < 3: continue
            
            filepath, line_num, line_text = parts
            clean_path = filepath.replace('.\\', '').replace('./', '')
            h_prefix = Path(clean_path).name[:10]
            
            if h_prefix != last_file:
                # New file block!
                if current_block:
                    if rendered_matches <= limit:
                        yield f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>"
                
                last_file = h_prefix
                meta = hash_prefix_to_meta.get(h_prefix, {})
                
                if filter_mode == 'tagged' and not meta.get('tag'):
                    current_block = None
                    continue

                seen_hashes.add(h_prefix)
                current_block = {
                    'hash': h_prefix,
                    'tag': meta.get('tag'),
                    'title': meta.get('title', ''),
                    'authors': meta.get('authors', ''),
                    'lines': []
                }

            if current_block:
                if is_match: total_matches += 1
                
                if rendered_matches < limit:
                    if is_match: rendered_matches += 1
                    
                    formatted_line = html.escape(line_text.rstrip())
                    if is_match:
                        try:
                            # Re-apply search pattern highlight
                            pat = re.compile(f'({re.escape(query)})', re.IGNORECASE)
                            formatted_line = pat.sub(r'<mark>\1</mark>', formatted_line)
                        except: pass

                    current_block['lines'].append({
                        'type': 'match' if is_match else 'context',
                        'number': line_num,
                        'text': formatted_line
                    })

        # Yield last block
        if current_block and rendered_matches <= limit:
            yield f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>"

        total_elapsed = time.time() - start_time
        rg_internal_time = parse_stats("".join(stats_buffer))
        
        if total_matches == 0:
             yield f"<div hx-swap-oob='innerHTML:#rg-results'><p class='muted'>No matches found.</p></div>"
        else:
            files_count = len(seen_hashes)
            stats_html = (
                f"<div class='d-flex align-items-center gap-3 text-muted small'>"
                f"<span>Searched ~<b>{total_docs_in_lib}</b> docs; found <b>{total_matches}</b> matches in <b>{files_count}</b> files</span>"
                f"<span class='border-start ps-3'>RG: {rg_internal_time}</span>"
                f"<span class='border-start ps-3'>Total: {total_elapsed:.3f}s</span>"
                f"</div>"
            )
            yield f"<div id='rg-stats-header' hx-swap-oob='innerHTML'>{stats_html}</div>"

            if total_matches > limit:
                more_html = (
                    f"<div class='alert alert-light border shadow-sm d-inline-block px-4 py-2 mt-3'>"
                    f"<p class='mb-2'>Showing {limit} of {total_matches} matches.</p>"
                    f"<button class='btn btn-primary btn-sm' onclick='alert(\"Feature: To see all results, refine your search or use the CLI for massive exports.\")'>How to see more?</button>"
                    f"</div>"
                )
                yield f"<div id='rg-more-container' hx-swap-oob='innerHTML' style='display: block;'>{more_html}</div>"


    return Response(stream_with_context(generate_results()), mimetype='text/html')

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
    return send_file(path, mimetype='application/pdf', as_attachment=False)

def get_path_for_tag(lib, tag):
    if lib.is_empty: return None
    doc_links = lib.ref_doc_df[lib.ref_doc_df.tag == tag]
    if doc_links.empty: return None
    doc_details = doc_links.merge(lib.doc_df, on=['hash', 'version'], how='inner')
    if doc_details.empty: return None
    rel_path = doc_details.iloc[0].path
    return lib.abspath(rel_path) if hasattr(lib, 'abspath') else rel_path

@bp.route('/view/<tag>')
def view(tag):
    lib = LibraryContext.get()
    path = get_path_for_tag(lib, tag)
    if not path or not Path(path).exists(): abort(404)
    return send_file(path, mimetype='application/pdf', as_attachment=False)

@bp.route('/status')
def status():
    lib = LibraryContext.get()
    if lib.is_empty: return "No library open."
    info = lib.get_status_info()
    status_info = {
        "name": info['name'],
        "config_path": info['path'],
        "doc_store": str(lib.doc_store_path),
        "ref_count": len(lib.ref_df),
        "doc_count": len(lib.doc_df),
        "ref_doc_count": len(lib.ref_doc_df),
        "files": info['files'],
        "needs_reload": info['needs_reload'],
        "watcher_active": info['watcher_active']
    }
    return render_template('status.html', status=status_info)

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

from ..import_bibtex import Bib2df_Incremental
from ..bibtex import dict_to_bibtex

@bp.route('/edit')
def editor_page():
    tag = request.args.get('tag', '').strip()
    return render_template('edit.html', initial_tag=tag)

@bp.route('/tag-suggest')
def tag_suggest():
    q = request.args.get('q', '').strip().lower()
    lib = LibraryContext.get()
    if not q or lib.is_empty: return ""
    
    # Simple prefix match for suggestions
    matches = [t for t in lib.all_tags if q in t.lower()][:10]
    return render_template('components/tag_suggestions.html', matches=matches)

@bp.route('/edit-tag/<tag>', methods=['GET', 'POST'])
def edit_tag(tag):
    lib = LibraryContext.get()
    if lib.is_empty: return "No library open."
    
    if request.method == 'GET':
        row = lib.ref_df[lib.ref_df.tag == tag]
        if row.empty: return f"Reference '{tag}' not found.", 404
        bib_str = dict_to_bibtex(row.iloc[0])
        return render_template('components/edit_form.html', tag=tag, bib_str=bib_str)
    
    # POST - Save changes
    bib_str = request.form.get('bibtex', '').strip()
    if not bib_str:
        return "BibTeX entry cannot be empty.", 400
        
    try:
        new_data = Bib2df_Incremental.parse_line(bib_str)
        if not new_data:
            return "Error parsing BibTeX entry.", 400
            
        lib.update_reference(tag, new_data)
        return render_template('components/edit_success.html', tag=new_data.get('tag', tag))
    except Exception as e:
        logger.error(f"Error updating reference {tag}: {e}")
        return f"Error: {str(e)}", 500
