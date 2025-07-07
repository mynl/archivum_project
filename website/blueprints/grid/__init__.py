from flask import Blueprint

grid_bp = Blueprint('grid', __name__, template_folder='../../templates')
from . import routes
