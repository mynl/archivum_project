from .shared import *

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

    try:
        spec = normalize_query(
            raw_query,
            default_limit=50,
            recent=True,
            projection="path, hash, type, *",
            default_empty_pattern=".",
        )

        df = lib.database
        result = df.querex(spec.expression)
        if not isinstance(result, pd.DataFrame) or result.empty:
            return "No results found for report generation.", 400

        # Generate QMD
        from ...quarto import generate_qmd_report
        generate_qmd_report(lib, result, out_path, title=title, intro_text=intro, query=raw_query, web_links=True)
        
        return {"status": "success", "id": out_path.stem}
    except Exception as e:
        logger.error(f"Generate error: {e}")
        return str(e), 500

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
