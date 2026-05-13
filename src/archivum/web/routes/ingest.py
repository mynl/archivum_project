from .shared import *

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
    from ...document import Document
    from ...hasher import hash_many3
    
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
        from ...bibtex import dict_to_bibtex
        data = doc.bib.copy()
        # Ensure authors are sorted Last, First
        if data.get('author'):
            data['author'] = Document._sort_authors(data['author'])
        data['tag'] = doc.key()
        bibtex = dict_to_bibtex(data)
    else:
        # If bibtex was pasted, also try to normalize the authors in it
        from ...bibtex import bibtex_to_dict, dict_to_bibtex
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

    from ...bibtex import bibtex_to_dict, dict_to_bibtex
    try:
        # Simple parser for single entry
        entries = bibtex_to_dict(bibtex)
        if not entries: return bibtex
        tag, data = list(entries.items())[0]
        data['tag'] = tag
        
        if action == 'names':
            authors = data.get('author', '')
            if authors:
                from ...document import Document
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
            from ...utilities import TagAllocator
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

    from ...bibtex import bibtex_to_dict
    from ...library import Library
    
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
                from ...enhancements import canonical_name_from_row
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
        from ...document import extract_text_for_paths
        fn = importer.doc_df.iloc[0].path
        extract_text_for_paths([lib.doc_store_path / fn], 
                               text_dir_path=lib.text_dir_path,
                               extractor=lib.config.extractor)

        return render_template(
            'components/alert.html',
            level='success',
            classes='mt-4',
            html_message=(
                f'Successfully archived <strong>{html.escape(str(tag))}</strong>! '
                f'<a href="/view/{html.escape(str(tag), quote=True)}" target="_blank">View PDF</a>'
            ),
        )
    
    except Exception as e:
        logger.error(f"Commit error: {e}")
        return render_template(
            'components/alert.html',
            level='danger',
            classes='mt-4',
            message=f"Error: {str(e)}",
        )

@bp.route('/view-temp/<filename>')
def view_temp(filename):
    temp_path = Path("temp/staging") / filename
    if not temp_path.exists(): abort(404)
    return send_file(str(temp_path.absolute()), mimetype='application/pdf')
