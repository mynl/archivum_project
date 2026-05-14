from .shared import *


REPORT_META_VERSION = 1


def _report_meta_path(lib, report_id):
    return lib.exports_dir_path / f"{report_id}.report.json"


def _load_report_meta(lib, report_id):
    meta_path = _report_meta_path(lib, report_id)
    if not meta_path.exists():
        return None
    try:
        with meta_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.warning(f"Failed to load report metadata for {report_id}: {e}")
        return None


def _write_report_meta(lib, out_path, *, title, filename, intro, raw_query, source, semantic_source, case_mode, include_abstract):
    now = datetime.now().isoformat(timespec="seconds")
    meta_path = _report_meta_path(lib, out_path.stem)
    previous = _load_report_meta(lib, out_path.stem) or {}
    data = {
        "version": REPORT_META_VERSION,
        "title": title,
        "filename": filename,
        "intro": intro,
        "query": raw_query,
        "source": source,
        "semantic_source": semantic_source,
        "case": case_mode,
        "include_abstract": include_abstract,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
    }
    meta_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data

@bp.route('/reports')
def reports_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    edit_id = request.args.get('edit', '').strip()
    edit_meta = None
    if edit_id:
        edit_meta = _load_report_meta(lib, edit_id)
        if edit_meta:
            query = str(edit_meta.get('query', '')).strip()
            source = str(edit_meta.get('source', 'query')).strip() or 'query'
            semantic_source = str(edit_meta.get('semantic_source', 'title')).strip() or 'title'
            case_mode = str(edit_meta.get('case', 'insensitive')).strip() or 'insensitive'
            include_abstract = bool(edit_meta.get('include_abstract', True))
        else:
            query = request.args.get('q', '').strip()
            source = request.args.get('source', 'query').strip() or 'query'
            semantic_source = request.args.get('semantic_source', request.args.get('source_type', 'title')).strip() or 'title'
            case_mode = request.args.get('case', 'insensitive').strip() or 'insensitive'
            include_abstract = request.args.get('abstract', '1') != '0'
    else:
        query = request.args.get('q', '').strip()
        source = request.args.get('source', 'query').strip() or 'query'
        semantic_source = request.args.get('semantic_source', request.args.get('source_type', 'title')).strip() or 'title'
        case_mode = request.args.get('case', 'insensitive').strip() or 'insensitive'
        include_abstract = request.args.get('abstract', '1') != '0'
    
    # Get existing reports
    reports = []
    try:
        # We look for .qmd files as the source of truth
        for p in lib.exports_dir_path.glob("*.qmd"):
            meta = _load_report_meta(lib, p.stem)
            reports.append({
                'id': p.stem,
                'name': p.name,
                'mtime': p.stat().st_mtime,
                'date': datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                'has_meta': meta is not None,
                'title': (meta or {}).get('title') or p.stem.replace('-', ' ').title(),
                'source': (meta or {}).get('source', ''),
            })
        reports.sort(key=lambda x: x['mtime'], reverse=True)
    except Exception as e:
        logger.error(f"Error listing reports: {e}")

    return render_template(
        'reports.html',
        lib=lib,
        query=query,
        reports=reports,
        source=source,
        semantic_source=semantic_source,
        case_mode=case_mode,
        include_abstract=include_abstract,
        edit_id=edit_id if edit_meta else "",
        edit_meta=edit_meta,
        edit_title=(edit_meta or {}).get('title', ''),
        edit_intro=(edit_meta or {}).get('intro', ''),
        edit_filename=re.sub(r'\.qmd$', '', str((edit_meta or {}).get('filename', ''))),
    )

@bp.route('/reports/generate', methods=['POST'])
@admin_required
def reports_generate():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    title = request.form.get('title', 'New Research Extract').strip()
    filename = request.form.get('filename', '').strip()
    intro = request.form.get('intro', '').strip()
    raw_query = request.form.get('query', '').strip()
    source = request.form.get('source', 'query').strip() or 'query'
    semantic_source = request.form.get('semantic_source', 'title').strip() or 'title'
    case_sensitive = request.form.get('case', 'insensitive') == 'sensitive'
    include_abstract = request.form.get('include_abstract', '1') != '0'

    if not filename:
        filename = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    if not filename.endswith('.qmd'):
        filename += '.qmd'

    out_path = lib.exports_dir_path / filename
    case_mode = 'sensitive' if case_sensitive else 'insensitive'

    try:
        if source == "semantic":
            from ...analytics.semantic import analyze_semantic
            from ...quarto import generate_semantic_qmd_report

            result = analyze_semantic(
                lib,
                raw_query,
                semantic_source,
                case_sensitive=case_sensitive,
            )
            if result.result_df.empty or result.relevant_idx.empty:
                return "No semantic results found for report generation.", 400
            generate_semantic_qmd_report(
                lib,
                result,
                out_path,
                title=title,
                intro_text=intro,
                include_abstract=include_abstract,
                query=raw_query,
                web_links=True,
            )
        elif source == "social":
            from ...analytics.networks import analyze_social_network
            from ...quarto import generate_social_qmd_report

            result = analyze_social_network(lib, raw_query, case_sensitive=case_sensitive)
            if result.result_df.empty:
                return "No social network results found for report generation.", 400
            generate_social_qmd_report(
                lib,
                result,
                out_path,
                title=title,
                intro_text=intro,
                include_abstract=include_abstract,
                query=raw_query,
                web_links=True,
            )
        else:
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

            from ...quarto import generate_qmd_report
            generate_qmd_report(
                lib,
                result,
                out_path,
                title=title,
                intro_text=intro,
                include_abstract=include_abstract,
                query=raw_query,
                web_links=True,
            )
        
        _write_report_meta(
            lib,
            out_path,
            title=title,
            filename=filename,
            intro=intro,
            raw_query=raw_query,
            source=source,
            semantic_source=semantic_source,
            case_mode=case_mode,
            include_abstract=include_abstract,
        )
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

@bp.route('/reports/asset/<path:asset_name>')
def reports_asset(asset_name):
    lib = LibraryContext.get()
    asset_path = (lib.exports_dir_path / asset_name).resolve()
    exports_root = lib.exports_dir_path.resolve()
    if exports_root not in asset_path.parents:
        abort(404)
    if not asset_path.exists() or asset_path.suffix.lower() not in {'.svg', '.png', '.jpg', '.jpeg'}:
        abort(404)
    return send_file(str(asset_path), as_attachment=False)

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
    for ext in ['.qmd', '.pdf', '.html', '.report.json']:
        p = lib.exports_dir_path / f"{report_id}{ext}"
        if p.exists():
            p.unlink()
            count += 1
    for suffix in ["social", "social-network", "semantic-hulls", "semantic-galaxy"]:
        p = lib.exports_dir_path / f"{report_id}-{suffix}.svg"
        if p.exists():
            p.unlink()
            count += 1
    return {"status": "deleted", "files": count}
