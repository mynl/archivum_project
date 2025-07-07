from flask import Blueprint

new_doc_bp = Blueprint('new_doc', __name__, template_folder='../../templates')
from . import routes
