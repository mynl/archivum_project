import tempfile

from .shared import *

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

    from ...quarto import QmdParser
    from ...bibtex import dict_to_bibtex

    # QmdParser reads from a path; a system temp dir keeps the pasted text out
    # of the checkout and is removed on exit
    with tempfile.TemporaryDirectory() as tmp:
        temp_file = Path(tmp) / "qmd_extract.qmd"
        temp_file.write_text(text, encoding='utf-8')
        parser = QmdParser(temp_file)
    # Use a more permissive regex for citations if the default one is too strict
    # The default is: r"(?<!@)@(?!REF)([A-Z][A-Za-z0-9]+)"
    # We'll allow lowercase starting tags too: r"(?<!@)@(?!REF)([A-Za-z0-9]+)"
    import re
    cite_rex = re.compile(r"(?<!@)@(?!REF)([A-Za-z][A-Za-z0-9]+)")
    tags = sorted(set([m.group(1) for m in cite_rex.finditer(text)]))
    
    logger.info(f"QMD Extraction found tags: {tags}")
    
    if not tags:
        return render_template(
            'components/alert.html',
            level='warning',
            message='No citations found (e.g. @Tag2023).',
        )

    # Match against library (case-insensitive if needed, but Archivum tags are usually case-sensitive)
    matches = lib.ref_df[lib.ref_df['tag'].isin(tags)]
    
    if matches.empty:
        # Try case-insensitive fallback for tags
        tags_lower = [t.lower() for t in tags]
        matches = lib.ref_df[lib.ref_df['tag'].str.lower().isin(tags_lower)]
        
    if matches.empty:
        return render_template(
            'components/alert.html',
            level='warning',
            message=f"Found {len(tags)} citations but none matched the library. Tags: {', '.join(tags)}",
        )

    bib_entries = [dict_to_bibtex(row) for _, row in matches.sort_values("tag").iterrows()]
    bib_text = "\n\n".join(bib_entries)
    
    return render_template('components/qmd_result.html', bib_text=bib_text, count=len(matches))
