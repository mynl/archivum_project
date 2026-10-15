from flask import make_response

from .shared import *
from ...bibtex import dict_to_bibtex
from ...import_bibtex import Bib2df_Incremental


def _render_edit_form(lib, tag, status_message=""):
    """The editor; ``status_message`` reports a save beside the buttons."""
    row = lib.ref_df[lib.ref_df.tag == tag]
    if row.empty:
        return f"Reference '{tag}' not found.", 404
    ref = row.iloc[0]
    bib_str = dict_to_bibtex(ref)
    # read-only: the date added is structural and stays out of the textarea
    added = ref.get("added")
    added = added.strftime("%Y-%m-%d") if added is not None and not pd.isna(added) else ""
    return render_template('components/edit_form.html', tag=tag, bib_str=bib_str, added=added,
                           documents_html=_render_documents(lib, tag),
                           status_message=status_message, status_level="success",
                           viewable=lib.primary_doc(tag) is not None)


def _edit_save_failed(message):
    """
    A refused save: the reason goes in the status slot and the edit stays in
    the box. Sent as 200 because htmx does not swap error responses.
    """
    response = make_response(render_template(
        'components/edit_status.html', tag="", status_message=message, status_level="danger"))
    response.headers["HX-Retarget"] = "#edit-status"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


def _size_display(size) -> str:
    """Bytes as kB or MB: document sizes span a few orders of magnitude."""
    if size is None or pd.isna(size):
        return ""
    size = float(size)
    return f"{size / 1e6:,.1f} MB" if size >= 1e6 else f"{size / 1e3:,.0f} kB"


def _render_documents(lib, tag, message="", level="info"):
    """The Documents panel: one row per link, with View, Make primary, Unlink."""
    rows = []
    for _, r in lib.linked_documents(tag).iterrows():
        created = r["create"]
        rows.append({
            "hash": str(r["hash"]),
            "version": int(r["version"]),
            "primary": int(r["priority"]) == 0,
            "name": "" if pd.isna(r["name"]) else str(r["name"]),
            "suffix": "" if pd.isna(r["suffix"]) else str(r["suffix"]).lstrip(".").upper(),
            "size": _size_display(r["size"]),
            "created": "" if pd.isna(created) else pd.Timestamp(created).strftime("%Y-%m-%d"),
            "reads": int(r["read_count"]),
            "orphan_on_unlink": int(r["shared_with"]) == 0,
        })
    return render_template('components/edit_documents.html', tag=tag, rows=rows,
                           message=message, level=level)


def _document_action(tag, action):
    """Run one mutating panel action and re-render the panel with its outcome."""
    lib = LibraryContext.get()
    if lib.is_empty:
        abort(404)
    if tag not in lib.ref_df.tag.values:
        return f"Reference '{tag}' not found.", 404
    try:
        message = action(lib)
        level = "success"
    except ValueError as e:
        message, level = str(e), "danger"
    except Exception as e:
        logger.error(f"Document action on {tag} failed: {e}")
        message, level = f"Error: {e}", "danger"
    return _render_documents(lib, tag, message=message, level=level)


def _form_link():
    """The (hash, version) a panel form posts."""
    file_hash = request.form.get('hash', '').strip()
    try:
        version = int(request.form.get('version', ''))
    except ValueError:
        raise ValueError("No document version given.")
    if not file_hash:
        raise ValueError("No document hash given.")
    return file_hash, version


@bp.route('/edit-tag/<tag>/documents')
@admin_required
def edit_documents(tag):
    lib = LibraryContext.get()
    if lib.is_empty:
        abort(404)
    return _render_documents(lib, tag)


@bp.route('/edit-tag/<tag>/documents/promote', methods=['POST'])
@admin_required
def edit_documents_promote(tag):
    def promote(lib):
        file_hash, version = _form_link()
        lib.promote_document(tag, file_hash, version)
        return f"{file_hash[:8].upper()} is now the primary document."
    return _document_action(tag, promote)


@bp.route('/edit-tag/<tag>/documents/unlink', methods=['POST'])
@admin_required
def edit_documents_unlink(tag):
    def unlink(lib):
        file_hash, version = _form_link()
        if not lib.unlink_document(tag, file_hash, version):
            raise ValueError(f"{file_hash[:8].upper()} was not linked to {tag}.")
        return f"Unlinked {file_hash[:8].upper()}. The file and its document row are kept."
    return _document_action(tag, unlink)


@bp.route('/edit-tag/<tag>/documents/link', methods=['POST'])
@admin_required
def edit_documents_link(tag):
    def link(lib):
        file_hash, version = lib.resolve_hash_prefix(request.form.get('hash_prefix', ''))
        if not lib.link_document(tag, file_hash, version, primary=False):
            raise ValueError(f"{file_hash[:8].upper()} is already linked to {tag}.")
        return f"Linked {file_hash[:8].upper()} as an alternate."
    return _document_action(tag, link)


# ------------------------------------------------------------- delete and merge

def _ref_or_404(lib, tag):
    """The reference row for ``tag``, or None."""
    rows = lib.ref_df[lib.ref_df.tag == tag]
    return None if rows.empty else rows.iloc[0]


def _sidebar_removal(tag):
    """Script that drops ``tag`` from the Edit sidebar once it is gone."""
    return render_template('components/sidebar_remove.html', tag=tag)


@bp.route('/edit-tag/<tag>/delete', methods=['GET', 'POST'])
@admin_required
def edit_delete(tag):
    """GET: the confirm panel. POST: delete, with an audit copy."""
    lib = LibraryContext.get()
    if lib.is_empty:
        abort(404)
    ref = _ref_or_404(lib, tag)
    if ref is None:
        return f"Reference '{tag}' not found.", 404
    if request.method == 'GET':
        return render_template(
            'components/edit_delete.html',
            tag=tag,
            title=clean_latex(str(ref.get("title", "") or "")),
            n_docs=len(lib.linked_documents(tag)),
            audit_dir=lib.config_path / "edit-audit",
            bibtex_file=lib.config.bibtex_file,
        )
    audit = lib.remove_reference(tag)
    return (
        render_template('components/edit_deleted.html', tag=tag, audit=audit)
        + _sidebar_removal(tag)
    )


def _render_merge_preview(lib, keep, absorb, message=""):
    """Proposed merged entry, conflicts, document plan, and the two actions."""
    from ...bibtex import propose_merge

    absorb = (absorb or "").strip()
    keep_ref, absorb_ref = _ref_or_404(lib, keep), _ref_or_404(lib, absorb)
    error = ""
    if not absorb:
        error = "Choose the reference to merge in."
    elif absorb == keep:
        error = "A reference cannot be merged into itself."
    elif keep_ref is None or absorb_ref is None:
        error = f"Reference '{keep if keep_ref is None else absorb}' not found."
    if error:
        return render_template('components/alert.html', level='warning', message=error)

    merged, conflicts = propose_merge(keep_ref, absorb_ref)
    keep_docs, absorb_docs = lib.linked_documents(keep), lib.linked_documents(absorb)
    have = set(zip(keep_docs.hash, keep_docs.version))
    plan = [
        {"tag": keep, "hash": h, "name": n, "outcome": "stays primary" if p == 0 else "stays"}
        for h, n, p in zip(keep_docs.hash, keep_docs.name, keep_docs.priority)
    ] + [
        {"tag": absorb, "hash": h, "name": n,
         "outcome": f"already on {keep}, dropped" if (h, v) in have else f"moves to {keep}"}
        for h, v, n in zip(absorb_docs.hash, absorb_docs.version, absorb_docs.name)
    ]
    return render_template(
        'components/edit_merge_preview.html',
        keep=keep,
        absorb=absorb,
        merged_bibtex=dict_to_bibtex(merged),
        conflicts=[{**c, "keep": clean_latex(str(c["keep"])), "absorb": clean_latex(str(c["absorb"]))}
                   for c in conflicts],
        plan=plan,
        bibtex_file=lib.config.bibtex_file,
        message=message,
    )


@bp.route('/edit-tag/<keep>/merge', methods=['GET', 'POST'])
@admin_required
def edit_merge(keep):
    """GET: choose the reference to merge in. POST: merge it, with an audit copy."""
    lib = LibraryContext.get()
    if lib.is_empty:
        abort(404)
    if _ref_or_404(lib, keep) is None:
        return f"Reference '{keep}' not found.", 404
    if request.method == 'GET':
        return render_template('components/edit_merge.html', keep=keep)

    absorb = request.form.get('absorb', '').strip()
    merged_fields = Bib2df_Incremental.parse_line(request.form.get('merged_bibtex', '').strip())
    if not merged_fields:
        return _render_merge_preview(lib, keep, absorb, message="Could not parse the merged BibTeX.")
    try:
        audit = lib.merge_references(keep, absorb, merged_fields)
    except ValueError as e:
        return _render_merge_preview(lib, keep, absorb, message=str(e))

    # success replaces the whole editor with the survivor's form
    form = _render_edit_form(lib, keep)
    html = (
        render_template('components/alert.html', level='success',
                        message=f"Merged {absorb} into {keep}. Audit copy: {audit}")
        + (form if isinstance(form, str) else form[0])
        + _sidebar_removal(absorb)
    )
    response = make_response(html)
    response.headers["HX-Retarget"] = "#editor-container"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@bp.route('/edit-tag/<keep>/merge/preview')
@admin_required
def edit_merge_preview(keep):
    lib = LibraryContext.get()
    if lib.is_empty:
        abort(404)
    # absorb from the Swap link, q from the tag input (shared with /tag-suggest)
    absorb = request.args.get('absorb') or request.args.get('q', '')
    return _render_merge_preview(lib, keep, absorb)


@bp.route('/edit-tag/<tag>/documents/hash-suggest')
@admin_required
def edit_documents_hash_suggest(tag):
    """Up to 8 documents whose hash starts with the typed prefix, with a linked tag."""
    lib = LibraryContext.get()
    prefix = request.args.get('hash_prefix', '').strip().lower()
    if lib.is_empty or len(prefix) < 4:
        return ""
    docs = lib.doc_df
    hits = docs[docs.hash.astype(str).str.lower().str.startswith(prefix)].drop_duplicates("hash").head(8)
    first_tag = lib.ref_doc_df.drop_duplicates("hash").set_index("hash")["tag"]
    matches = [
        {"hash": str(h), "name": str(n), "tag": str(first_tag.get(h, ""))}
        for h, n in zip(hits.hash, hits.get("name", hits.hash))
    ]
    return render_template('components/hash_suggestions.html', matches=matches)


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
        return _edit_save_failed("BibTeX entry cannot be empty.")

    try:
        new_data = Bib2df_Incremental.parse_line(bib_str)
        if not new_data:
            return _edit_save_failed("Error parsing BibTeX entry.")
        lib.update_reference(tag, new_data)
    except ValueError as e:
        return _edit_save_failed(str(e))
    except Exception as e:
        logger.error(f"Error updating reference {tag}: {e}")
        return _edit_save_failed(f"Error: {e}")

    # the editor reloads with the saved entry, as Ingest's workbench resets
    new_tag = new_data.get('tag', tag)
    if new_tag == tag:
        return _render_edit_form(lib, tag, status_message="updated")
    return (
        _render_edit_form(lib, new_tag, status_message=f"updated, renamed from {tag}")
        + render_template('components/sidebar_rename.html', old_tag=tag, new_tag=new_tag)
    )

@bp.route('/help')
def help_page():
    lib = LibraryContext.get()
    return render_template('help.html', lib=lib)
