from flask import Blueprint, redirect, url_for, current_app, request
import webbrowser

from . services import generate_pdf_path

view_pdf_bp = Blueprint("view_pdf", __name__)


@view_pdf_bp.route("/view")
def view_pdf():
    n = int(request.args.get("i"))
    print(f'searching for doc {n}')
    lib = current_app.lib
    pdf_path = generate_pdf_path(n, lib)
    if pdf_path is not None:
        webbrowser.open_new_tab(pdf_path.as_uri())
    return redirect(url_for("querex.home"))  # or some other page


