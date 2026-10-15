from .shared import *

@bp.route('/health')
@admin_required
def health_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    
    action = request.args.get('action')
    if action == 'clean_extracts':
        lib.clean_text_extracts(execute=True)
        invalidate_cached_data('audit')
    
    def calc_audit():
        return lib.audit()
        
    findings = get_cached_data(lib, 'audit', calc_audit)
    return render_template('health.html', lib=lib, findings=findings)


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


@bp.route('/about')
def about_page():
    """Versions of archivum, its packages, front end, and external tools. Read-only, open to all."""
    from ...versions import collect_versions
    from ..app import FRONTEND_VERSIONS

    # the no-library placeholder may carry no config
    config = getattr(LibraryContext.get(), "config", None)
    ddjvu = getattr(config, "ddjvu_command", None) or "ddjvu"
    versions = collect_versions(ddjvu_command=ddjvu, frontend=FRONTEND_VERSIONS)
    return render_template('about.html', versions=versions)

