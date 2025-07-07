from flask import render_template, current_app
from . import home_bp


@home_bp.route("/")
@home_bp.route("/index.html")
@home_bp.route("/home.html")
def home():
    return render_template("home.html", active_page="home")

