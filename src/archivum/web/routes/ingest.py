from .shared import *


def _render_ingest_preview(lib, bibtex, temp_path, known_hash):
    try:
        preview = lib.preview_staged_document_import(
            bibtex,
            temp_path,
            known_hash=known_hash,
            source_label="web-ingest-preview",
        )
    except Exception as e:
        logger.error(f"Preview error: {e}")
        preview = {
            "bibtex": bibtex,
            "tag": "",
            "analysis": pd.DataFrame(),
            "blocked": True,
            "blocked_message": f"Preview error: {str(e)}",
        }
    return render_template(
        "_ingest_preview.html",
        preview_bibtex=preview["bibtex"],
        preview_tag=preview["tag"],
        preview_blocked=preview["blocked"],
        preview_message=preview["blocked_message"],
        analysis_rows=preview["analysis"].to_dict("records") if not preview["analysis"].empty else [],
    )

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

    preview_html = _render_ingest_preview(lib, bibtex, temp_path, doc.hash)

    return render_template('_ingest_workbench.html', 
                           lib=lib, 
                           doc=doc, 
                           bibtex=bibtex, 
                           preview_html=preview_html,
                           temp_path=str(temp_path),
                           temp_filename=temp_filename,
                           duplicate_tag=duplicate_tag)

@bp.route('/ingest/preview', methods=['POST'])
@admin_required
def ingest_preview():
    lib = LibraryContext.get()
    bibtex = request.form.get('bibtex', '')
    temp_path = Path(request.form.get('temp_path'))
    h = request.form.get('hash')
    return _render_ingest_preview(lib, bibtex, temp_path, h)

@bp.route('/ingest/commit', methods=['POST'])
@admin_required
def ingest_commit():
    lib = LibraryContext.get()
    bibtex = request.form.get('bibtex')
    temp_path = Path(request.form.get('temp_path'))
    h = request.form.get('hash')

    from ...library import LibraryImportBlocked
    
    try:
        importer = lib.import_staged_document(
            bibtex,
            temp_path,
            known_hash=h,
            source_label="web-ingest",
        )
        tag = str(importer.ref_df.iloc[0].tag)

        return render_template(
            'components/alert.html',
            level='success',
            classes='mt-4',
            html_message=(
                f'Successfully archived <strong>{html.escape(str(tag))}</strong>! '
                f'<a href="/view/{html.escape(str(tag), quote=True)}" target="_blank">View PDF</a>'
            ),
        )
    except LibraryImportBlocked as e:
        return render_template(
            'components/alert.html',
            level='danger',
            classes='mt-4',
            message=str(e),
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
