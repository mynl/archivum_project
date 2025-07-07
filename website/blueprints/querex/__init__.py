from flask import Blueprint

querex_bp = Blueprint('querex', __name__, template_folder='../../templates')
from . import routes
