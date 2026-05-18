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

