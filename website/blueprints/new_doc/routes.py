from flask import render_template, request, current_app, send_from_directory
from . import new_doc_bp
from .services import process_new_pdf


@new_doc_bp.route("/new", methods=["GET"])
def new():
    pdf_dir = current_app.config['PDF_DIR']
    lib = current_app.lib
    print(pdf_dir)
    pdf_paths = sorted(p for p in pdf_dir.glob("*.pdf"))
    pdfs = [i.name for i in pdf_paths]
    selected_filename = request.args.get("file", pdfs[0] if pdfs else None)

    dummy_metadata_html = '<strong>Metadata not yet set</strong>'
    bibtex = "None available."
    titles = ("", "")

    if selected_filename:
        selected_pdf_path = None
        for p in pdf_paths:
            if p.name == selected_filename:
                selected_pdf_path = p
                break

        if selected_pdf_path:
            try:
                dummy_metadata_html, bibtex, titles = process_new_pdf(selected_pdf_path, lib)
            except Exception as e:
                # Handle potential errors during PDF processing or metadata extraction
                dummy_metadata_html = f"<strong style='color:red;'>Error processing PDF: {e}</strong>"
                bibtex = "Error: Could not generate BibTeX."
                titles = ('Error?', 'Error?')

    return render_template("new.html",
        active_page="new",
        pdfs=pdfs,
        selected=selected_filename,
        metadata=dummy_metadata_html,
        title_1=titles[0],
        title_2=titles[1],
        bibtex=bibtex)


@new_doc_bp.route("/pdfs/<path:filename>")
def serve_pdf(filename):
    pdf_dir = current_app.config['PDF_DIR']
    return send_from_directory(pdf_dir, filename)
