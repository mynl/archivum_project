from .shared import *
from ...bibtex import dict_to_bibtex
from ...import_bibtex import Bib2df_Incremental


def _render_edit_form(lib, tag):
    row = lib.ref_df[lib.ref_df.tag == tag]
    if row.empty:
        return f"Reference '{tag}' not found.", 404
    bib_str = dict_to_bibtex(row.iloc[0])
    return render_template('components/edit_form.html', tag=tag, bib_str=bib_str)


@bp.route('/edit')
@admin_required
def editor_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    tag = request.args.get('tag', '').strip()
    initial_edit_html = ''
    initial_edit_status = 200
    if tag:
        rendered = _render_edit_form(lib, tag)
        if isinstance(rendered, tuple):
            initial_edit_html, initial_edit_status = rendered
        else:
            initial_edit_html = rendered
    return render_template(
        'edit.html',
        initial_tag=tag,
        initial_edit_html=initial_edit_html,
        initial_edit_status=initial_edit_status,
    )

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
        return _render_edit_form(lib, tag)
    
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
