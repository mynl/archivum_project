from flask import render_template, request
from . import generic_bp


@generic_bp.route("/", endpoint="home")
def generic():
    return render_template("generic/home.html", active_page="home")


@generic_bp.route("/about")
def about():
    return render_template("about.html", active_page="about")


@generic_bp.route("/work")
def work():
    return render_template("work.html", active_page="work")


@generic_bp.route("/echo")
def echo_page():
    return render_template("echo.html", active_page="echo")


@generic_bp.route("/echo", methods=["POST"])
def echo_text():
    text_content = request.form.get("textBoxContent", "")
    return text_content

