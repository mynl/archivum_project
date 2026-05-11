from flask import Blueprint, render_template, request, send_file, abort, Response, stream_with_context, url_for, make_response, g
from functools import wraps
from ..cli import LibraryContext
from ..utilities import trim_author, clean_latex
from ..quarto import generate_qmd_report
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
import numpy as np

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

# Global transformer model to avoid redundant loading
_MODEL_CACHE = {
    'transformer': None
}

def get_transformer_model():
    """Lazy-load the transformer model."""
    if _MODEL_CACHE['transformer'] is None:
        logger.info("Loading SentenceTransformer model 'all-MiniLM-L6-v2'...")
        from sentence_transformers import SentenceTransformer
        _MODEL_CACHE['transformer'] = SentenceTransformer('all-MiniLM-L6-v2')
    return _MODEL_CACHE['transformer']

def _resolve_universe(lib, raw_query):
    """Robustly resolve document hashes for a combined querex/ripgrep query."""
    querex_part = ""; ripgrep_part = ""
    if ' rg ' in raw_query.lower():
        parts = re.split(r'\s+rg\s+', raw_query, flags=re.IGNORECASE, maxsplit=1)
        querex_part = parts[0].strip(); ripgrep_part = parts[1].strip()
        if querex_part.lower().startswith('q '): querex_part = querex_part[2:].strip()
    elif raw_query.lower().startswith('q '): querex_part = raw_query[2:].strip()
    elif raw_query.lower().startswith('rg '): ripgrep_part = raw_query[3:].strip()
    else: querex_part = raw_query

    df = lib.database
    
    # 1. Querex Phase
    querex_hashes = set()
    if querex_part:
        # Construct valid querex: [TOP N] [RECENT] SELECT ...
        q_expr = querex_part
        if 'select' not in q_expr.lower():
            # Match top/recent prefixes
            match = re.match(r'^((?:top\s+\d+\s+)?(?:recent\s+)?)(.*)$', q_expr, re.IGNORECASE)
            if match:
                prefix, rest = match.groups()
                q_expr = f"{prefix}select hash, tag, author, title, year, * {rest}"
            else:
                q_expr = "select hash, tag, author, title, year, * " + q_expr
        
        q_result = df.querex(q_expr)
        if not isinstance(q_result, pd.DataFrame):
            raise ValueError(f"Querex error: {q_result}")
        querex_hashes = set(q_result['hash'].dropna().astype(str))
    else:
        querex_hashes = set(df['hash'].dropna().astype(str))

    # 2. Ripgrep Phase
    if ripgrep_part:
        rg_hashes = set()
        is_regex = any(c in ripgrep_part for c in r".*+?^$|()[]{}")
        args = ["-n", "-H", "--pcre2"] if is_regex else ["-n", "-H"]
        
        # Handle -g flags in ripgrep_part
        clean_rg = ripgrep_part
        if ' -g ' in ripgrep_part:
            rg_bits = ripgrep_part.split(' -g ')
            clean_rg = rg_bits[0].strip()
            for g in rg_bits[1:]: args.extend(['-g', g.strip()])
        else:
            args.extend(['-g', '*.md'])
        
        rc, proc = lib.run_ripgrep(clean_rg, args)
        for line in proc.stdout:
            line = line.strip()
            if not line or ':' not in line: continue
            parts = line.split(':', 2)
            if len(parts) < 3: continue
            h_prefix = Path(parts[0]).name[:10].upper()
            # Map prefix to full hashes in our current querex set
            for h in querex_hashes:
                if h.upper().startswith(h_prefix):
                    rg_hashes.add(h)
        return rg_hashes
    
    return querex_hashes

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

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not getattr(g, 'is_admin', False):
            return "Permission Denied: Read-only mode enabled for external access.", 403
        return f(*args, **kwargs)
    return decorated_function

@bp.before_app_request
def check_for_reload():
    """Check if the library needs a reload due to external changes."""
    lib = LibraryContext.get()
    if not lib.is_empty and lib.needs_reload:
        logger.info(f"External changes detected. Reloading library '{lib.name}' in web context.")
        lib.reset()
        LibraryContext.refresh()

def _render_export_button(id_prefix, hashes, input_id='rg-input'):
    """Helper to render the full export button group for OOB swaps."""
    # We use btn-info for active state instead of btn-outline-info
    return (
        f"<div id='{id_prefix}-export-container' class='btn-group shadow-sm' hx-swap-oob='true'>"
        f"    <button id='{id_prefix}-export-btn' "
        f"            onclick=\"handleExportToQuery('{id_prefix}', '{input_id}')\""
        f"            class='btn btn-info px-4 fw-bold' "
        f"            data-hashes='{hashes}'"
        f"            title='Export documents to Query screen'>"
        f"        Export"
        f"    </button>"
        f"    <button id='{id_prefix}-export-toggle' "
        f"            type='button' "
        f"            class='btn btn-info dropdown-toggle dropdown-toggle-split' "
        f"            data-bs-toggle='dropdown' "
        f"            aria-expanded='false'>"
        f"        <span class='visually-hidden'>Toggle Dropdown</span>"
        f"    </button>"
        f"    <ul class='dropdown-menu dropdown-menu-end shadow'>"
        f"        <li><a class='dropdown-item' href='#' onclick=\"handleExportToQuery('{id_prefix}', '{input_id}')\">To Query (Max 500)</a></li>"
        f"        <li><a class='dropdown-item' href='#' onclick=\"handleExportToCSV('{id_prefix}', '{input_id}')\">To CSV (All matches)</a></li>"
        f"    </ul>"
        f"</div>"
    )

@bp.route('/health')
@admin_required
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
@admin_required
def ingest_page():
    lib = LibraryContext.get()
    return render_template('ingest.html', lib=lib)

@bp.route('/ingest/start', methods=['POST'])
@admin_required
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
@admin_required
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
@admin_required
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
        
        # Add header with Author name, Export, and Options
        def render_radio(mode, label):
            checked = 'checked' if view_mode == mode else ''
            return (
                f"<li><div class='dropdown-item py-1'><div class='form-check'>"
                f"  <input class='form-check-input' type='radio' name='author-view-mode' "
                f"         id='author-mode-{mode}' value='{mode}' {checked} "
                f"         hx-get=\"{url_for('main.author_search', author=author, view_mode=mode)}\" "
                f"         hx-target='#author-results'> "
                f"  <label class='form-check-label small w-100 cursor-pointer' for='author-mode-{mode}'>{label}</label>"
                f"</div></div></li>"
            )

        header_oob = (
            f"<div id='author-results-header' hx-swap-oob='true' class='d-flex flex-wrap align-items-center gap-2 mb-4'>"
            f"  <h4 class='mb-0 fw-bold me-auto'><i class='bi bi-person-fill me-2'></i>{author}</h4>"
            f"  <button class='btn btn-info px-4 fw-bold shadow-sm' onclick=\"exportAuthorToQuery('{author}')\">"
            f"    Export"
            f"  </button>"
            f"  <div class='dropdown'>"
            f"    <button class='btn btn-outline-secondary px-3 shadow-sm dropdown-toggle' type='button' data-bs-toggle='dropdown'>"
            f"      <i class='bi bi-gear-fill me-1'></i> Options"
            f"    </button>"
            f"    <ul class='dropdown-menu dropdown-menu-end shadow-sm' style='min-width: 200px;'>"
            f"      <li><h6 class='dropdown-header text-uppercase small pb-1'>View Mode</h6></li>"
            f"      {render_radio('list', 'Dense List')}"
            f"      {render_radio('verbose', 'Verbose')}"
            f"      {render_radio('table', 'Table')}"
            f"    </ul>"
            f"  </div>"
            f"</div>"
        )
        
        return header_oob + _render_search_results(result, view_mode=view_mode)
        
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

    # Safely handle hash for display
    hash_col = find_col(display_results, 'hash')
    if hash_col:
        display_results['hash_display'] = display_results[hash_col].fillna('').astype(str).str[:8]
    else:
        display_results['hash_display'] = ""

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

@bp.route('/rg-warm')
def rg_warm():
    """Warming route to build metadata cache for Ripgrep."""
    lib = LibraryContext.get()
    if not lib.is_empty:
        get_hash_meta_cache(lib)
    return "OK"

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
            
            # Join with doc_df to get size
            meta_df = latest_links.merge(lib.doc_df, on=['hash', 'version'], how='left')
            # Join with ref_df to get title/author/year
            meta_df = meta_df.merge(lib.ref_df, on='tag', how='left')

            for _, row in meta_df.iterrows():
                prefix = str(row.hash)[:10]
                hash_prefix_to_meta[prefix] = {
                    'tag': row.tag,
                    'title': str(row.get('title', '')).replace('{', '').replace('}', ''),
                    'authors': str(row.get('author', '')), # Keep raw for splitting
                    'year': str(row.get('year', '9999')).split('.')[0],
                    'publisher': str(row.get('publisher', '')) if pd.notna(row.get('publisher')) else '',
                    'size': int(row.get('size', 0)) if pd.notna(row.get('size')) else 0
                }
        _META_CACHE_GLOBAL['lib_name'] = lib.name
        _META_CACHE_GLOBAL['mtime'] = mtime
        _META_CACHE_GLOBAL['data'] = hash_prefix_to_meta

    return _META_CACHE_GLOBAL['data']

# Ripgrep Result Cache
_RG_CACHE = {
    'lib_name': None,
    'last_sync': None,
    'date': None,
    'data': {},      # key -> {'counts': dict, 'cmd': str}
    'html': {},      # key -> rendered_html_fragments
    'stats': {}      # key -> {matches: X, docs: Y, cmd: str}
}

def get_rg_cache_item(lib, key, subkey='html'):
    global _RG_CACHE
    lib_id = lib.name
    feather_path = lib.config_path / "ref.feather"
    last_sync = feather_path.stat().st_mtime if feather_path.exists() else 0
    today = datetime.now().date()
    
    if (_RG_CACHE['lib_name'] != lib_id or 
        _RG_CACHE['last_sync'] != last_sync or 
        _RG_CACHE['date'] != today):
        logger.info(f"Invalidating RG cache for {lib_id}")
        _RG_CACHE = {'lib_name': lib_id, 'last_sync': last_sync, 'date': today, 'data': {}, 'html': {}, 'stats': {}}
    
    return _RG_CACHE[subkey].get(key)

def set_rg_cache_item(key, value, subkey='html'):
    global _RG_CACHE
    if len(_RG_CACHE[subkey]) > 100:
        _RG_CACHE[subkey].pop(next(iter(_RG_CACHE[subkey])))
    _RG_CACHE[subkey][key] = value

@bp.route('/rg-search')
def rg_search():
    query = request.args.get('q', '').strip()
    show_files = request.args.get('files') == 'true'

    if not query and not show_files:
        return ""

    context_a = request.args.get('after', '0')
    context_b = request.args.get('before', '0')
    show_counts = request.args.get('counts') == 'true'
    show_summary = request.args.get('summary') == 'true'
    if show_summary: show_counts = True

    case_sensitive = request.args.get('case') == 'sensitive'
    glob1 = request.args.get('glob1', '').strip()
    glob2 = request.args.get('glob2', '').strip()
    filter_mode = request.args.get('filter', 'tagged')

    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."

    hash_prefix_to_meta = get_hash_meta_cache(lib)
    total_docs_in_lib = len(lib.doc_df)

    # Unique key for this search configuration
    mode = 'details'
    if show_summary: mode = 'summary'
    elif show_counts: mode = 'counts'
    
    search_key = f"{query}_{mode}_{filter_mode}_{case_sensitive}_{glob1}_{glob2}"
    if mode == 'details':
        search_key += f"_{context_a}_{context_b}"

    # --- TOP LEVEL CACHE CHECK ---
    if not show_files:
        cached_html = get_rg_cache_item(lib, search_key, 'html')
        stats_meta = get_rg_cache_item(lib, search_key, 'stats')
        if cached_html and stats_meta:
            logger.info(f"RG HTML Cache hit for: {query} ({mode})")
            m, d = stats_meta.get('matches', 0), stats_meta.get('docs', 0)
            hashes = stats_meta.get('hashes', '')
            verb = "Summarized" if (mode in ['summary', 'counts']) else "Found"
            noun = "documents" if (mode in ['summary', 'counts']) else "files"

            cache_tag = (
                f"<div id='rg-stats-header' hx-swap-oob='true'>"
                f"<div class='text-muted small mt-n3 mb-3'>"
                f"<i class='bi bi-lightning-fill text-warning me-1'></i> "
                f"{verb} <b>{m}</b> matches in <b>{d}</b> {noun}. (Retrieved from cache)"
                f"</div></div>"
            )

            export_oob = ""
            if hashes:
                # Ensure hashes from cache are also 8-char if they weren't already
                h_list = [h[:8] for h in hashes.split('|')]
                export_oob = _render_export_button('rg', "|".join(h_list))

            # Spacing fix: rg-status margin reduced
            status_fix = f"<div id='rg-status' hx-swap-oob='true' class='rg-info' style='margin-bottom: 0.25rem; display: block;'>Last Command: <code>(Retrieved from Cache)</code></div>"
            return cached_html + cache_tag + status_fix + export_oob

    def generate_results():
        start_time = time.time()
        html_buffer = []
        final_stats = {'matches': 0, 'docs': 0}

        def yield_and_buffer(chunk):
            html_buffer.append(chunk)
            return chunk

        # 1. Reset UI areas
        yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true'></div>")
        yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'></div>")
        yield yield_and_buffer(f"<div id='rg-more-container' hx-swap-oob='true' style='display: none;'></div>")

        def format_glob(g):
            if not g: return None
            if '*' in g or '?' in g or '[' in g: return g
            if '.' in g: return f'*{g}*'
            return f'*{g}*.md'

        is_regex = any(c in query for c in r".*+?^$|()[]{}")
        common_args = []
        if not is_regex: 
            common_args.append('-F')
        elif any(p in query for p in ['(?=', '(?!', '(?<=', '(?<!']):
            # PCRE2 is required for look-around assertions
            common_args.append('--pcre2')

        g1 = format_glob(glob1)
        if g1: common_args.extend(['--iglob', g1])
        g2 = format_glob(glob2)
        if g2: common_args.extend(['--iglob', g2])
        if not (g1 or g2): common_args.extend(['-g', '*.md'])

        if show_files:
            cmd = ['rg', '--files'] + common_args + [str(lib.text_dir_path)]
            yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>{' '.join(cmd)}</code></div>")
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                files = []
                for line in proc.stdout:
                    h_prefix = Path(line.strip()).name[:10]
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    if filter_mode == 'tagged' and not meta.get('tag'): continue
                    files.append({'hash': h_prefix, 'tag': meta.get('tag'), 'title': meta.get('title', h_prefix)})
                yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true'>{render_template('components/rg_files.html', files=files)}</div>")
                stats_html = f"<div class='text-muted small mb-3'>Found <b>{len(files)}</b> files in {time.time() - start_time:.3f}s</div>"
                yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'>{stats_html}</div>")
            except Exception as e:
                yield yield_and_buffer(f"<div class='error' hx-swap-oob='true'>Ripgrep Error: {str(e)}</div>")
            return

        # 4. Summary / Counts Mode
        if show_counts:
            try:
                data_key = f"{query}_{filter_mode}_{case_sensitive}_{glob1}_{glob2}"
                counts = get_rg_cache_item(lib, data_key, 'data')
                rg_internal_time = "0.000s"

                if counts is None:
                    args = ["-n", "-H"] + common_args
                    if not case_sensitive: args.append('-i')
                    rc, proc = lib.run_ripgrep(query, args)
                    yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>rg {html.escape(' '.join(args))} \"{query}\"</code></div>")

                    counts, stats_buffer, is_stats_section = {}, [], False
                    for line in proc.stdout:
                        if not line.strip(): continue
                        if re.match(r'^\s*\d+ matches', line) or re.match(r'^\s*\d+ matched lines', line): is_stats_section = True
                        if is_stats_section: stats_buffer.append(line); continue
                        parts = line.split(':', 2)
                        if len(parts) < 3: continue
                        h_prefix = Path(parts[0]).name[:10]
                        meta = hash_prefix_to_meta.get(h_prefix, {})
                        if filter_mode == 'tagged' and not meta.get('tag'): continue
                        counts[h_prefix] = counts.get(h_prefix, 0) + 1

                    m = re.findall(r'(\d+\.\d+) seconds', "".join(stats_buffer))
                    rg_internal_time = f"{float(m[-1]):.3f}s" if m else "0.000s"
                    set_rg_cache_item(data_key, counts, 'data')
                else:
                    yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>(Retrieved from Cache)</code></div>")

                counts_list = []
                total_m = 0
                for h_prefix, count_val in counts.items():
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    counts_list.append({'hash': h_prefix, 'count': count_val, 'tag': meta.get('tag'), 'title': meta.get('title', ''), 'authors': meta.get('authors', '')})
                    total_m += count_val
                counts_list.sort(key=lambda x: x['count'], reverse=True)

                final_stats['matches'] = total_m
                final_stats['docs'] = len(counts_list)

                if show_summary:
                    year_data, author_data = {}, {}
                    total_papers_set = set()
                    for h_prefix, count_val in counts.items():
                        meta = hash_prefix_to_meta.get(h_prefix, {})
                        year, size = meta.get('year', '9999'), meta.get('size', 0)
                        total_papers_set.add(h_prefix)
                        
                        if year not in year_data: year_data[year] = {'papers': 0, 'matches': 0, 'size': 0, 'hashes': set()}
                        year_data[year]['papers'] += 1
                        year_data[year]['matches'] += count_val
                        year_data[year]['size'] += size
                        year_data[year]['hashes'].add(h_prefix[:8])

                        raw_authors = meta.get('authors', 'Unknown')
                        author_list = [a.strip().replace('{', '').replace('}', '') for a in raw_authors.split(' and ')]
                        for auth in author_list:
                            if not auth or auth == "Unknown": continue
                            if auth not in author_data: author_data[auth] = {'papers': 0, 'matches': 0, 'size': 0, 'hashes': set()}
                            author_data[auth]['papers'] += 1
                            author_data[auth]['matches'] += count_val
                            author_data[auth]['size'] += size
                            author_data[auth]['hashes'].add(h_prefix[:8])

                    total_papers_count = len(total_papers_set)
                    def prepare_rows(data):
                        if not data: return []
                        max_matches = max(v['matches'] for v in data.values())
                        rows = []
                        for label, vals in data.items():
                            hash_query = f"hash ~ /{ '|'.join(list(vals.get('hashes', []))[:50]) }/" if 'hashes' in vals else ""
                            rows.append({
                                'label': label, 'papers': vals['papers'], 'papers_pct': (vals['papers'] / total_papers_count * 100),
                                'matches': vals['matches'], 'matches_pct': (vals['matches'] / total_m * 100),
                                'spark_pct': (vals['matches'] / max_matches * 100), 'mtc_pap': vals['matches'] / vals['papers'],
                                'mtc_100kb': (vals['matches'] / (vals['size'] / 102400)) if vals['size'] else 0, 'hash_query': hash_query
                            })
                        return rows
                    year_rows = sorted(prepare_rows(year_data), key=lambda x: x['label'], reverse=True)
                    author_rows = sorted(prepare_rows(author_data), key=lambda x: x['matches'], reverse=True)[:100]
                    summary_html = render_template('components/rg_summary.html', year_rows=year_rows, author_rows=author_rows, totals={'papers': total_papers_count, 'matches': total_m, 'author_count': len(author_data)})
                    yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true' class='mt-5'>{summary_html}</div>")
                else:
                    yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true' class='mt-4'>{render_template('components/rg_counts.html', counts=counts_list)}</div>")

                # EXPORT BUTTON OOB SWAP
                top_hashes = "|".join([x['hash'][:8] for x in counts_list[:500]])
                if top_hashes:
                    yield yield_and_buffer(_render_export_button('rg', top_hashes))

                final_stats['matches'] = total_m
                final_stats['docs'] = len(counts_list)
                final_stats['hashes'] = top_hashes

                stats_html = f"<div class='text-muted small mt-n3 mb-3'>Summarized <b>{total_m}</b> matches in <b>{len(counts_list)}</b> documents. Total: {time.time() - start_time:.3f}s (RG: {rg_internal_time})</div>"
                yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'>{stats_html}</div>")
                set_rg_cache_item(search_key, "".join(html_buffer), 'html')
                set_rg_cache_item(search_key, final_stats, 'stats')
            except Exception as e:
                logger.error(f"RG Summary/Counts Error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                yield yield_and_buffer(f"<div class='error' hx-swap-oob='true'>Ripgrep Summary Error: {str(e)}</div>")
            return

        # 5. Details Mode
        try:
            args = ["-n", "-H"] + common_args
            if not case_sensitive: args.append('-i')
            args.extend(['-A', context_a, '-B', context_b])
            rc, proc = lib.run_ripgrep(query, args)
            yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>rg {html.escape(' '.join(args))} \"{query}\"</code></div>")

            limit, rendered_matches, total_matches, seen_hashes = 500, 0, 0, set()
            current_block, last_file, stats_buffer, is_stats_section = None, None, [], False

            for line in proc.stdout:
                if not line.strip(): continue
                if re.match(r'^\s*\d+ matches', line) or re.match(r'^\s*\d+ matched lines', line): is_stats_section = True
                if is_stats_section: stats_buffer.append(line); continue
                is_match = ':' in line
                is_context = '-' in line and not is_match
                if not (is_match or is_context): continue
                sep = ':' if is_match else '-'
                parts = line.split(sep, 2)
                if len(parts) < 3: continue
                h_prefix = Path(parts[0]).name[:10]
                if h_prefix != last_file:
                    if current_block and rendered_matches <= limit:
                        yield yield_and_buffer(f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>")
                    last_file = h_prefix
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    if filter_mode == 'tagged' and not meta.get('tag'): current_block = None; continue
                    seen_hashes.add(h_prefix)
                    current_block = {'hash': h_prefix, 'tag': meta.get('tag'), 'title': meta.get('title', ''), 'authors': meta.get('authors', ''), 'lines': []}
                if current_block:
                    if is_match: total_matches += 1
                    if rendered_matches < limit:
                        if is_match: rendered_matches += 1
                        formatted_line = html.escape(parts[2].rstrip())
                        if is_match:
                            try:
                                pat = re.compile(f'({re.escape(query)})', re.IGNORECASE); formatted_line = pat.sub(r'<mark>\1</mark>', formatted_line)
                            except: pass
                        current_block['lines'].append({'type': 'match' if is_match else 'context', 'number': parts[1], 'text': formatted_line})

            if current_block and rendered_matches <= limit:
                yield yield_and_buffer(f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>")

            m = re.findall(r'(\d+\.\d+) seconds', "".join(stats_buffer))
            rg_internal_time = f"{float(m[-1]):.3f}s" if m else "0.000s"
            final_stats['matches'] = total_matches
            final_stats['docs'] = len(seen_hashes)
            
            # EXPORT BUTTON OOB SWAP
            top_hashes = "|".join([h[:8] for h in list(seen_hashes)[:50]])
            if top_hashes:
                yield yield_and_buffer(_render_export_button('rg', top_hashes))
                final_stats['hashes'] = top_hashes

            stats_html = f"<div class='text-muted small mt-n3 mb-3'>Found <b>{total_matches}</b> matches in <b>{len(seen_hashes)}</b> files. Total: {time.time() - start_time:.3f}s (RG: {rg_internal_time})</div>"
            yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'>{stats_html}</div>")
            set_rg_cache_item(search_key, "".join(html_buffer), 'html')
            set_rg_cache_item(search_key, final_stats, 'stats')
        except Exception as e:
            logger.error(f"RG Details Error: {e}")
            yield yield_and_buffer(f"<div class='error' hx-swap-oob='true'>Ripgrep Error: {str(e)}</div>")
    
    
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
    
    # Record read
    lib.record_read(h, caller=request.referrer or "")
    
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
    
    # Resolve hash for recording
    doc_links = lib.ref_doc_df[lib.ref_doc_df.tag == tag]
    if not doc_links.empty:
        h = doc_links.iloc[0].hash
        lib.record_read(h, caller=request.referrer or "")

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
@admin_required
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
@admin_required
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

@bp.route('/help')
def help_page():
    lib = LibraryContext.get()
    return render_template('help.html', lib=lib)

@bp.route('/reports')
def reports_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    query = request.args.get('q', '').strip()
    
    # Get existing reports
    reports = []
    try:
        # We look for .qmd files as the source of truth
        for p in lib.exports_dir_path.glob("*.qmd"):
            reports.append({
                'id': p.stem,
                'name': p.name,
                'mtime': p.stat().st_mtime,
                'date': datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            })
        reports.sort(key=lambda x: x['mtime'], reverse=True)
    except Exception as e:
        logger.error(f"Error listing reports: {e}")

    return render_template('reports.html', lib=lib, query=query, reports=reports)

@bp.route('/reports/generate', methods=['POST'])
@admin_required
def reports_generate():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    title = request.form.get('title', 'New Research Extract').strip()
    filename = request.form.get('filename', '').strip()
    intro = request.form.get('intro', '').strip()
    raw_query = request.form.get('query', '').strip()

    if not filename:
        filename = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    if not filename.endswith('.qmd'):
        filename += '.qmd'

    out_path = lib.exports_dir_path / filename

    # Standardize query
    lower_query = raw_query.lower()
    if lower_query.startswith('q '):
        search_type = 'q'; query = raw_query[2:].strip()
    elif lower_query.startswith('f '):
        search_type = 'f'; query = raw_query[2:].strip()
    else:
        search_type = 'f'; query = raw_query

    try:
        if search_type == 'f':
            if not query or (query[0] != "!" and query.find("~") == -1):
                query_expr = f"recent top 50 select path, hash, type, * tag ~ {query or '.'}"
            else:
                query_expr = query
                if "select" not in query_expr.lower(): query_expr = "select path, hash, type, * " + query_expr
                if "top" not in query_expr.lower(): query_expr = "top 50 " + query_expr
                if "recent" not in query_expr.lower(): query_expr = "recent " + query_expr
        else:
            query_expr = query
            if "select" not in query_expr.lower(): query_expr = "select path, hash, type, * " + query_expr

        df = lib.database
        result = df.querex(query_expr)
        if not isinstance(result, pd.DataFrame) or result.empty:
            return "No results found for report generation.", 400

        # Generate QMD
        from ..quarto import generate_qmd_report
        generate_qmd_report(lib, result, out_path, title=title, intro_text=intro, query=raw_query, web_links=True)
        
        return {"status": "success", "id": out_path.stem}
    except Exception as e:
        logger.error(f"Generate error: {e}")
        return str(e), 500

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
        universe_hashes = _resolve_universe(lib, raw_query)
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
        df = lib.database
        universe_hashes = _resolve_universe(lib, raw_query)
        result_df = df[df['hash'].astype(str).isin(universe_hashes)].copy()
        
        if result_df.empty: 
            return {"elements": [], "papers": 0, "clusters": []}

        # Phase 2: Semantic Index & Omission
        idx_path = lib.config_path / "semantic-embeddings.feather"
        idx_df = pd.read_feather(idx_path) if idx_path.exists() else pd.DataFrame(columns=['hash', 'source', 'embedding'])
        
        to_embed = []
        omitted_hashes = []
        
        for _, row in result_df.iterrows():
            h = str(row.hash)
            match = idx_df[(idx_df.hash == h) & (idx_df.source == source_type)]
            if match.empty:
                if source_type == 'text':
                    # Check for extract existence
                    from ..document import Document
                    doc = Document(lib.abspath(row.path))
                    doc.hash = h
                    txt_p = doc.text_path(lib.text_dir_path, lib.config.extractor)
                    if not txt_p.exists():
                        omitted_hashes.append(h)
                        continue
                to_embed.append(row)

        # Remove omitted from results
        result_df = result_df[~result_df.hash.astype(str).isin(omitted_hashes)]
        if result_df.empty:
            return {"elements": [], "papers": 0, "omitted_count": len(omitted_hashes), "omitted_reason": "No text extracts found.", "clusters": []}

        if to_embed:
            model = get_transformer_model()
            new_rows = []
            for row in to_embed:
                text = ""
                if source_type == 'text':
                    from ..document import Document
                    doc = Document(lib.abspath(row.path))
                    doc.hash = str(row.hash)
                    txt_p = doc.text_path(lib.text_dir_path, lib.config.extractor)
                    text = txt_p.read_text(encoding='utf-8', errors='ignore')[:2000]
                else:
                    text = f"{row.title}. {row.author}."
                
                emb = model.encode(text).tolist()
                new_rows.append({'hash': str(row.hash), 'source': source_type, 'embedding': emb})
            
            idx_df = pd.concat([idx_df, pd.DataFrame(new_rows)]).drop_duplicates(['hash', 'source'], keep='last')
            idx_df.reset_index(drop=True).to_feather(idx_path)

        # Phase 3: Math (UMAP + HDBSCAN)
        relevant_idx = idx_df[(idx_df.hash.isin(result_df.hash.astype(str))) & (idx_df.source == source_type)]
        import umap
        import hdbscan
        
        embeddings = np.array(relevant_idx['embedding'].tolist())
        n_pts = len(embeddings)
        
        # Project (Use cosine for text)
        n_neighbors = min(n_pts - 1, 15)
        if n_neighbors < 2:
            coords = np.random.rand(n_pts, 2) * 200
        else:
            reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=0.1, n_components=2, metric='cosine', random_state=42)
            coords = reducer.fit_transform(embeddings)

        # Cluster
        min_cluster = max(2, min(n_pts, 5))
        if n_pts < min_cluster:
            cluster_labels = np.zeros(n_pts)
        else:
            try:
                clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster)
                cluster_labels = clusterer.fit_predict(coords)
            except:
                cluster_labels = np.zeros(n_pts)

        # Phase 4: Thematic Naming & Formatting
        stop_words = {'the', 'and', 'for', 'with', 'model', 'analysis', 'using', 'based', 'from', 'study', 'results', 'data', 'approach', 'system', 'research', 'paper', 'new', 'proposed'}
        cluster_themes = {}
        
        # Pre-calculate themes
        unique_cids = sorted([cid for cid in set(cluster_labels) if cid >= 0])
        for cid in unique_cids:
            indices = [i for i, l in enumerate(cluster_labels) if l == cid]
            cluster_hashes = [relevant_idx.iloc[i].hash for i in indices]
            cluster_titles = result_df[result_df.hash.astype(str).isin(cluster_hashes)].title.dropna().tolist()
            
            # Simple keyword extraction
            words = []
            for t in cluster_titles:
                tokens = re.findall(r'\b[a-z]{4,}\b', t.lower())
                words.extend([w for w in tokens if w not in stop_words])
            
            top_words = [w for w, _ in Counter(words).most_common(3)]
            cluster_themes[cid] = ", ".join(top_words).title() if top_words else f"Constellation {cid}"

        elements = []
        current_year = datetime.now().year
        palette = ['#0d6efd', '#198754', '#ffc107', '#dc3545', '#0dcaf0', '#6610f2', '#fd7e14', '#20c997']
        
        cluster_summary = []
        for idx, cid in enumerate(unique_cids):
            indices = [i for i, l in enumerate(cluster_labels) if l == cid]
            cluster_hashes = [relevant_idx.iloc[i].hash for i in indices]
            sample_titles = result_df[result_df.hash.astype(str).isin(cluster_hashes)].title.head(3).tolist()
            cluster_summary.append({
                'id': int(cid),
                'number': idx + 1,
                'name': cluster_themes[cid],
                'count': len(indices),
                'samples': [clean_latex(t) for t in sample_titles],
                'color': palette[int(cid) % len(palette)]
            })

        for i, (_, row_idx) in enumerate(relevant_idx.iterrows()):
            matches = result_df[result_df.hash.astype(str) == row_idx.hash]
            if matches.empty: continue
            meta = matches.iloc[0]
            
            cid = int(cluster_labels[i])
            try: year = int(str(meta.year).split('.')[0])
            except: year = 2000
            
            opacity = max(0.4, 1.0 - (max(0, current_year - year) / 30.0))
            
            # Determine mapping number for display
            c_num = ""
            if cid >= 0:
                for cs in cluster_summary:
                    if cs['id'] == cid:
                        c_num = cs['number']
                        break

            elements.append({
                'data': {
                    'id': f"paper-{row_idx.hash}", 'tag': str(meta.tag), 'title': clean_latex(str(meta.title)),
                    'authors': trim_author(str(meta.author)), 'year': year, 'cluster_id': cid,
                    'cluster_number': c_num,
                    'cluster_name': cluster_themes.get(cid, "Lone Star"),
                    'color': palette[cid % len(palette)] if cid >= 0 else '#adb5bd',
                    'opacity': opacity
                },
                'position': {'x': float(coords[i][0] * 120), 'y': float(coords[i][1] * 120)}
            })

        status = f'<i class="bi bi-stars me-2"></i> Galaxy mapped: {len(elements)} papers, {len(cluster_summary)} clusters.'
        if verbosity == 'verbose':
            status += f" (Omitted: {len(omitted_hashes)} | Embeddings: {len(to_embed)})"

        return {
            "elements": elements, "papers": len(result_df), "omitted_count": len(omitted_hashes),
            "omitted_reason": "No text extracts found for these papers.",
            "hashes": "|".join(result_df['hash'].dropna().astype(str).str[:8].unique()[:500]),
            "status_msg": status,
            "clusters": cluster_summary
        }
    except Exception as e:
        logger.error(f"Semantic analysis error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500

@bp.route('/rg-export-csv')
def rg_export_csv():
    query = request.args.get('q', '').strip()
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    # Use current mode to find cache key
    # CSV export always uses current search's counts data
    mode = 'counts' # Default to counts for data extraction
    
    # We need to find the data cache key. Since we don't know the exact 
    # filters the user had (unless we pass them all), we'll try to reconstruct 
    # or rely on the query if passed.
    # For now, let's look for any 'data' cache item for this query.
    # Or better: just re-run the counts logic if not cached (it's fast).
    
    # Note: We'll need a simplified version of the search logic to get matches
    hash_prefix_to_meta = get_hash_meta_cache(lib)
    is_regex = any(c in query for c in r".*+?^$|()[]{}")
    args = ["-n", "-H"]
    if not is_regex: args.append('-F')
    elif any(p in query for p in ['(?=', '(?!', '(?<=', '(?<!']): args.append('--pcre2')
    
    # Run RG to get hashes and match counts
    rc, proc = lib.run_ripgrep(query, args)
    counts = {}
    for line in proc.stdout:
        parts = line.split(':', 2)
        if len(parts) < 3: continue
        h_prefix = Path(parts[0]).name[:10]
        counts[h_prefix] = counts.get(h_prefix, 0) + 1

    if not counts:
        return "No matches found to export.", 400

    # Map prefixes to full hashes using database.name as the source of truth
    # prefix -> full_hash
    prefix_to_full = {}
    if not lib.database.empty:
        # We use the 'name' column which starts with the 10-char hash prefix
        # and map it to the 'hash' column
        for _, row in lib.database[['name', 'hash']].iterrows():
            if pd.isna(row['name']): continue
            prefix_to_full[str(row['name'])[:10]] = row['hash']

    data_rows = []
    df = lib.database
    
    for h_prefix, count in counts.items():
        full_hash = prefix_to_full.get(h_prefix)
        if not full_hash:
            # Fallback: try direct lookup in case the prefix is actually the hash
            match = df[df['hash'].astype(str).str.startswith(h_prefix)]
        else:
            match = df[df['hash'] == full_hash]
            
        if match.empty: continue
        
        row = match.iloc[0].to_dict()
        row['matches'] = count
        data_rows.append(row)

    if not data_rows:
        return "Failed to map matches to database.", 500

    export_df = pd.DataFrame(data_rows)
    
    # Reorder columns for sanity
    cols = ['tag', 'author', 'title', 'year', 'publisher', 'journal', 'type', 'matches', 'path', 'hash']
    existing_cols = [c for c in cols if c in export_df.columns]
    remaining = [c for c in export_df.columns if c not in existing_cols]
    export_df = export_df[existing_cols + remaining]

    # Filename generation: arc-MM-DD-shortened-query.csv
    date_str = datetime.now().strftime("%m-%d")
    clean_q = re.sub(r'[^a-zA-Z0-9]+', '-', query).strip('-')[:30]
    filename = f"arc-{date_str}-{clean_q}.csv"

    # Save to temp and send
    temp_path = Path("temp") / filename
    export_df.to_csv(temp_path, index=False, encoding='utf-8-sig') # BOM for Excel
    
    return send_file(str(temp_path.absolute()), as_attachment=True, download_name=filename)

@bp.route('/search-export-csv')
def search_export_csv():
    raw_query = request.args.get('q', '').strip()
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query:
        return "No query provided.", 400

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

    try:
        if search_type == 'f':
            if query[0] != "!" and query.find("~") == -1:
                # Fuzzy search, export all matches (no top 50 limit)
                query_expr = f"select * tag ~ {query}"
            else:
                query_expr = query
                if "select" not in query_expr.lower():
                    query_expr = "select * " + query_expr
                # We don't force 'top 50' or 'recent' for export unless the user did
        else:
            query_expr = query
            if "select" not in query_expr.lower():
                query_expr = "select * " + query_expr

        df = lib.database
        export_df = df.querex(query_expr)

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

@bp.route('/semantic-export-csv')
def semantic_export_csv():
    raw_query = request.args.get('q', '').strip()
    source_type = request.args.get('source', 'title')
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    if not raw_query: return "No query provided.", 400

    try:
        # 1. Get the data (This re-uses the semantic_data logic but returns a CSV)
        # To avoid code duplication, we'd ideally refactor semantic_data, 
        # but for a quick fix we can call it or replicate the core parts.
        
        # We'll re-run the universe resolution and clustering
        # (It will be fast due to the embedding cache)
        
        # [Universe Resolution - Replicated from semantic_data]
        df = lib.database
        querex_part = ""; ripgrep_part = ""
        if ' rg ' in raw_query.lower():
            parts = re.split(r'\s+rg\s+', raw_query, flags=re.IGNORECASE, maxsplit=1)
            querex_part = parts[0].strip(); ripgrep_part = parts[1].strip()
            if querex_part.lower().startswith('q '): querex_part = querex_part[2:].strip()
        elif raw_query.lower().startswith('q '): querex_part = raw_query[2:].strip()
        elif raw_query.lower().startswith('rg '): ripgrep_part = raw_query[3:].strip()
        else: querex_part = raw_query

        querex_hashes = set()
        if querex_part:
            q_df = df.querex(f"select hash {querex_part}")
            if isinstance(q_df, pd.DataFrame):
                querex_hashes = set(q_df['hash'].dropna().astype(str))
        else:
            querex_hashes = set(df['hash'].dropna().astype(str))

        rg_hashes = set()
        if ripgrep_part:
            is_regex = any(c in ripgrep_part for c in r".*+?^$|()[]{}")
            args = ["-n", "-H", "--pcre2"] if is_regex else ["-n", "-H"]
            rc, proc = lib.run_ripgrep(ripgrep_part, args)
            for line in proc.stdout:
                line = line.strip()
                if not line or ':' not in line: continue
                parts = line.split(':', 2)
                h_prefix = Path(parts[0]).name[:10].upper()
                for h in querex_hashes:
                    if h.upper().startswith(h_prefix): rg_hashes.add(h)
            universe_hashes = rg_hashes
        else:
            universe_hashes = querex_hashes

        result_df = df[df['hash'].astype(str).isin(universe_hashes)].copy()
        if result_df.empty: return "No matches found.", 400

        # [Clustering - Replicated from semantic_data]
        idx_path = lib.config_path / "semantic-embeddings.feather"
        if not idx_path.exists(): return "No embeddings found. Run analysis in browser first.", 400
        
        idx_df = pd.read_feather(idx_path)
        relevant_idx = idx_df[(idx_df.hash.isin(result_df.hash.astype(str))) & (idx_df.source == source_type)]
        
        if relevant_idx.empty: return "No cached embeddings for this set.", 400

        import numpy as np
        import umap
        import hdbscan
        
        embeddings = np.array(relevant_idx['embedding'].tolist())
        n_pts = len(embeddings)
        
        # Project & Cluster
        n_neighbors = min(n_pts - 1, 15)
        if n_neighbors < 2:
            cluster_labels = np.zeros(n_pts)
        else:
            reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=0.1, n_components=2, random_state=42)
            coords = reducer.fit_transform(embeddings)
            min_cluster = min(n_pts, 5)
            clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster)
            cluster_labels = clusterer.fit_predict(coords)

        # Map clusters back to result_df
        hash_to_cluster = {str(row.hash): int(cluster_labels[i]) for i, (_, row) in enumerate(relevant_idx.iterrows())}
        
        result_df['constellation'] = result_df['hash'].astype(str).map(hash_to_cluster).fillna(-1).astype(int)
        result_df['constellation'] = result_df['constellation'].apply(lambda x: f"Constellation {x}" if x >= 0 else "Lone Star")

        # Reorder columns
        cols = ['constellation', 'tag', 'author', 'title', 'year', 'publisher', 'journal', 'hash']
        existing_cols = [c for c in cols if c in result_df.columns]
        remaining = [c for c in result_df.columns if c not in existing_cols]
        export_df = result_df[existing_cols + remaining]

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

@bp.route('/qmd')
def qmd_page():
    lib = LibraryContext.get()
    return render_template('qmd.html', lib=lib)

@bp.route('/qmd/extract', methods=['POST'])
def qmd_extract():
    lib = LibraryContext.get()
    text = request.form.get('text', '').strip()
    uploaded_file = request.files.get('file')

    if uploaded_file and uploaded_file.filename:
        text = uploaded_file.read().decode('utf-8', errors='ignore')
    
    if not text:
        return "No text or file provided.", 400

    # Write to a temporary file for QmdParser
    temp_file = Path("temp/qmd_extract.qmd")
    temp_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file.write_text(text, encoding='utf-8')

    from ..quarto import QmdParser
    from ..bibtex import dict_to_bibtex
    
    parser = QmdParser(temp_file)
    # Use a more permissive regex for citations if the default one is too strict
    # The default is: r"(?<!@)@(?!REF)([A-Z][A-Za-z0-9]+)"
    # We'll allow lowercase starting tags too: r"(?<!@)@(?!REF)([A-Za-z0-9]+)"
    import re
    cite_rex = re.compile(r"(?<!@)@(?!REF)([A-Za-z][A-Za-z0-9]+)")
    tags = sorted(set([m.group(1) for m in cite_rex.finditer(text)]))
    
    logger.info(f"QMD Extraction found tags: {tags}")
    
    if not tags:
        return "<div class='alert alert-warning'>No citations found (e.g. @Tag2023).</div>"

    # Match against library (case-insensitive if needed, but Archivum tags are usually case-sensitive)
    matches = lib.ref_df[lib.ref_df['tag'].isin(tags)]
    
    if matches.empty:
        # Try case-insensitive fallback for tags
        tags_lower = [t.lower() for t in tags]
        matches = lib.ref_df[lib.ref_df['tag'].str.lower().isin(tags_lower)]
        
    if matches.empty:
        return f"<div class='alert alert-warning'>Found {len(tags)} citations but none matched the library. Tags: {', '.join(tags)}</div>"

    bib_entries = [dict_to_bibtex(row) for _, row in matches.sort_values("tag").iterrows()]
    bib_text = "\n\n".join(bib_entries)
    
    return render_template('components/qmd_result.html', bib_text=bib_text, count=len(matches))

@bp.route('/reports/view/<report_id>')
def reports_view(report_id):
    lib = LibraryContext.get()
    qmd_path = lib.exports_dir_path / f"{report_id}.qmd"
    html_path = lib.exports_dir_path / f"{report_id}.html"
    if not qmd_path.exists(): abort(404)

    # Cache check: if .html exists and is newer than .qmd, serve it
    if html_path.exists() and html_path.stat().st_mtime >= qmd_path.stat().st_mtime:
        try:
            html_content = html_path.read_text(encoding='utf-8')
            title = report_id.replace('-', ' ').title()
            return render_template('reports.html', lib=lib, view_mode=True, report_html=html_content, report_title=title)
        except Exception as e:
            logger.warning(f"Failed to read cached HTML for {report_id}: {e}")

    try:
        # --citeproc for citations, -t html for fragment.
        # Explicitly set encoding='utf-8' for Windows compatibility.
        cmd = ['pandoc', str(qmd_path), '--citeproc', '-t', 'html']
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        html_content = res.stdout
        
        # Save to cache
        try:
            html_path.write_text(html_content, encoding='utf-8')
        except Exception as e:
            logger.warning(f"Failed to cache HTML for {report_id}: {e}")

        title = report_id.replace('-', ' ').title()
        return render_template('reports.html', lib=lib, view_mode=True, report_html=html_content, report_title=title)
    except Exception as e:
        logger.error(f"Pandoc render failed: {e}")
        return f"Error rendering report: {str(e)}", 500

@bp.route('/reports/raw/<report_id>')
def reports_raw(report_id):
    lib = LibraryContext.get()
    qmd_path = lib.exports_dir_path / f"{report_id}.qmd"
    if not qmd_path.exists(): abort(404)
    return send_file(str(qmd_path), mimetype='text/plain', as_attachment=False)

@bp.route('/reports/pdf/<report_id>')
@admin_required
def reports_pdf(report_id):
    lib = LibraryContext.get()
    qmd_path = lib.exports_dir_path / f"{report_id}.qmd"
    pdf_path = lib.exports_dir_path / f"{report_id}.pdf"
    if not qmd_path.exists(): abort(404)

    # Cache check: if .pdf exists and is newer than .qmd, serve it
    if pdf_path.exists() and pdf_path.stat().st_mtime >= qmd_path.stat().st_mtime:
        return send_file(str(pdf_path), mimetype='application/pdf', as_attachment=True)

    try:
        cmd = ['quarto', 'render', str(qmd_path), '--to', 'pdf']
        subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=str(lib.exports_dir_path), encoding='utf-8')
        if not pdf_path.exists(): return "PDF generation failed", 500
        return send_file(str(pdf_path), mimetype='application/pdf', as_attachment=True)
    except Exception as e:
        logger.error(f"PDF build error: {e}")
        return str(e), 500

@bp.route('/reports/delete/<report_id>', methods=['POST'])
@admin_required
def reports_delete(report_id):
    lib = LibraryContext.get()
    count = 0
    for ext in ['.qmd', '.pdf', '.html']:
        p = lib.exports_dir_path / f"{report_id}{ext}"
        if p.exists():
            p.unlink()
            count += 1
    return {"status": "deleted", "files": count}
