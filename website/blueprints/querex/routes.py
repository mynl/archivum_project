from flask import render_template, request, current_app
from . import querex_bp
from .services import perform_rfuzz_search, new_search
from greater_tables import GT # Assuming GT is installed

@querex_bp.route("/querex", methods=["GET", "POST"])
def querex():
    query_str = request.form.get("query", "").strip() if request.method == "POST" else ""
    result_html = ""
    lib = current_app.lib

    if query_str:
        try:
            result_html = new_search(query_str, lib)
        except Exception as e:
            result_html = f"<pre style='color:red'>{e}</pre>"

    if request.headers.get("HX-Request"):
        return result_html
    else:
        return render_template("querex.html", active_page="querex", query=query_str, result=result_html)


# original
@querex_bp.route("/querex-old", methods=["GET", "POST"])
def querex_old():
    query_str = request.form.get("query", "").strip() if request.method == "POST" else ""
    result_html = ""
    lib = current_app.lib

    if query_str:
        try:
            result_df = perform_rfuzz_search(query_str, lib)
            result_html = GT(result_df.head(200),
                large_ok=True,
                max_table_inch_width=12,
                year_cols=['year', 'index']
                ).html
        except Exception as e:
            result_html = f"<pre style='color:red'>{e}</pre>"

    if request.headers.get("HX-Request"):
        return result_html
    else:
        return render_template("querex.html", active_page="querex", query=query_str, result=result_html)
