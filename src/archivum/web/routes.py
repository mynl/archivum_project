from flask import Blueprint, render_template, request, send_file, abort, Response, stream_with_context
from ..cli import LibraryContext
import pandas as pd
from pathlib import Path
import logging
import json
import html
import subprocess
import os

logger = logging.getLogger(__name__)

bp = Blueprint('main', __name__)

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

@bp.route('/insights')
def insights_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    # 1. Total Counts
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
        # Check both journal and booktitle
        sources = pd.concat([lib.ref_df.get('journal', pd.Series()), lib.ref_df.get('booktitle', pd.Series())])
        top_journals = sources[sources != ""].value_counts().head(10).to_dict().items()
        
    # 4. Top Years
    top_years = []
    if not lib.ref_df.empty and 'year' in lib.ref_df.columns:
        top_years = lib.ref_df['year'].value_counts().head(10).to_dict().items()
        # Sort by year instead of count
        top_years = sorted(top_years, key=lambda x: str(x[0]), reverse=True)
        
    # 5. Top Publishers
    top_publishers = []
    if not lib.ref_df.empty and 'publisher' in lib.ref_df.columns:
        top_publishers = lib.ref_df['publisher'][lib.ref_df.publisher != ""].value_counts().head(10).to_dict().items()

    return render_template('insights.html', 
                           lib=lib,
                           total_refs=total_refs,
                           total_docs=total_docs,
                           total_orphans=total_orphans,
                           top_authors=top_authors,
                           top_journals=top_journals,
                           top_years=top_years,
                           top_publishers=top_publishers)

@bp.route('/')
def index():
    return render_template('query.html')

def trim_author(s):
    """Clean author string: short names, truncate at 3 with et al. if more."""
    if not isinstance(s, str) or not s:
        return ""
    # Split and clean
    author_bits = [i.split(",")[0].strip("{} ") for i in s.split(" and ")]
    if len(author_bits) > 3:
        name = ", ".join(author_bits[:3]) + " et al."
    elif len(author_bits) == 3:
        name = ", ".join(author_bits[:2]) + f", and {author_bits[2]}"
    elif len(author_bits) == 2:
        name = f"{author_bits[0]} and {author_bits[1]}"
    else:
        name = author_bits[0] if author_bits else ""
    return name.replace('{', '').replace('}', '')

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
            # Ensure we select type for book detection even in fuzzy mode
            # querexfuzz 'f' mode (tag ~ ...) usually returns all columns if not specified,
            # but we explicitly add them if we are constructing the expression.
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

        display_results = result.copy()
        
        def find_col(df, target):
            cols = {c.lower(): c for c in df.columns}
            return cols.get(target.lower())

        type_col = find_col(display_results, 'type')
        year_col = find_col(display_results, 'year')
        title_col = find_col(display_results, 'title')
        author_col = find_col(display_results, 'author')

        def clean_latex(s):
            if not isinstance(s, str):
                return ""
            return s.replace('{', '').replace('}', '')

        # Clean strings
        for col in [title_col, author_col, find_col(display_results, 'journal'), find_col(display_results, 'publisher')]:
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

        view_mode = request.args.get('view_mode', 'list')
        return render_template('components/results.html', results=display_results, view_mode=view_mode)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"<div class='error'>Error: {str(e)}</div>"

@bp.route('/ripgrep')
def ripgrep_page():
    return render_template('ripgrep.html')

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

    # Cache metadata lookup once
    hash_prefix_to_meta = {}
    if not lib.ref_doc_df.empty:
        latest_links = lib.ref_doc_df.sort_values(['hash', 'version'], ascending=[True, False]).drop_duplicates('hash')
        meta_df = latest_links.merge(lib.ref_df, on='tag', how='left')
        for _, row in meta_df.iterrows():
            prefix = str(row.hash)[:10]
            hash_prefix_to_meta[prefix] = {
                'tag': row.tag,
                'title': str(row.get('title', '')).replace('{', '').replace('}', ''),
                'authors': trim_author(row.get('author', ''))
            }

    def generate_results():
        logger.info(f"Starting rg_search generator for query: {query}, filter: {filter_mode}")
        if show_files:
            cmd = ['rg', '--files']
            if glob1: cmd.extend(['-g', f'*{glob1}*.md'])
            if glob2: cmd.extend(['-g', f'*{glob2}*.md'])
            cmd.append(str(lib.text_dir_path))
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
                    
                    if filter_mode == 'tagged' and not meta.get('tag'):
                        continue

                    files.append({
                        'hash': h_prefix,
                        'tag': meta.get('tag'),
                        'title': meta.get('title', h_prefix)
                    })
                yield f"<div hx-swap-oob='innerHTML:#rg-results'>{render_template('components/rg_files.html', files=files)}</div>"
            except Exception as e:
                yield f"<div class='error' hx-swap-oob='beforeend:#rg-results'>Ripgrep Error: {str(e)}</div>"
            return

        # Normal search or counts
        args = []
        if not case_sensitive:
            args.append('-i')
        if not show_counts:
            args.extend(['-A', context_a, '-B', context_b])
        if glob1:
            args.extend(['-g', f'*{glob1}*.md'])
        if glob2:
            args.extend(['-g', f'*{glob2}*.md'])
        
        rc, proc = lib.run_ripgrep(query, args)
        rg_cmd = f"rg --json --line-buffered --stats -C 1 {' '.join(args)} \"{query}\" {lib.text_dir_full_name}"
        yield f"<div id='rg-status' class='rg-info' style='margin-bottom: 1rem; display: block;' hx-swap-oob='true'>Last Command: <code>{html.escape(rg_cmd)}</code></div>"

        if show_counts:
            counts = {}
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                    if event.get('type') == 'match':
                        path_text = event['data'].get('path', {}).get('text', '')
                        h_prefix = Path(path_text).name[:10]
                        counts[h_prefix] = counts.get(h_prefix, 0) + 1
                except: continue
            
            counts_list = []
            for h_prefix, count_val in counts.items():
                meta = hash_prefix_to_meta.get(h_prefix, {})
                
                if filter_mode == 'tagged' and not meta.get('tag'):
                    continue

                counts_list.append({
                    'hash': h_prefix, 
                    'count': count_val,
                    'tag': meta.get('tag'),
                    'title': meta.get('title', ''),
                    'authors': meta.get('authors', '')
                })
            counts_list.sort(key=lambda x: x['count'], reverse=True)
            yield f"<div hx-swap-oob='innerHTML:#rg-results'>{render_template('components/rg_counts.html', counts=counts_list)}</div>"
            return

        # Streaming Blocks for normal search
        current_block = None
        match_count = 0
        for line in proc.stdout:
            try:
                event = json.loads(line)
                etype = event.get('type')
                if etype == 'begin':
                    path_text = event['data'].get('path', {}).get('text')
                    h_prefix = Path(path_text).name[:10]
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    
                    if filter_mode == 'tagged' and not meta.get('tag'):
                        current_block = None
                        continue

                    current_block = {
                        'hash': h_prefix,
                        'tag': meta.get('tag'),
                        'title': meta.get('title', ''),
                        'authors': meta.get('authors', ''),
                        'lines': []
                    }
                elif etype in ['match', 'context'] and current_block:
                    data = event['data']
                    line_text = data.get('lines', {}).get('text', '')
                    line_number = data.get('line_number')
                    submatches = data.get('submatches', [])
                    
                    formatted_line = ""
                    last_pos = 0
                    for match in sorted(submatches, key=lambda x: x['start']):
                        start, end = match['start'], match['end']
                        formatted_line += html.escape(line_text[last_pos:start])
                        formatted_line += f'<mark>{html.escape(line_text[start:end])}</mark>'
                        last_pos = end
                    formatted_line += html.escape(line_text[last_pos:])
                    
                    current_block['lines'].append({
                        'type': event['type'],
                        'number': line_number,
                        'text': formatted_line
                    })
                elif etype == 'end' and current_block:
                    match_count += 1
                    # Yield the completed block OOB - appends to results
                    yield f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>"
                    current_block = None
            except: continue

        if match_count == 0:
             yield f"<div hx-swap-oob='innerHTML:#rg-results'><p class='muted'>No matches found.</p></div>"

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

@bp.route('/history')
def history():
    lib = LibraryContext.get()
    if lib.is_empty: return "No library open."
    df = lib.history()
    if not df.empty and 'created' in df.columns:
        df = df.sort_values(by='created', ascending=False)
    elif not df.empty:
        # Fallback to first column if 'created' is missing
        sort_col = next((c for c in df.columns if c.lower() in ['date', 'timestamp', 'time']), df.columns[0])
        df = df.sort_values(by=sort_col, ascending=False)
    return render_template('history.html', history=df)

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

@bp.route('/sync-check')
def sync_check():
    lib = LibraryContext.get()
    return ("reload-needed", 200) if lib.needs_reload else ("ok", 200)
