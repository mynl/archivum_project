from flask import Blueprint

generic_bp = Blueprint(
    "generic",
    __name__,
    url_prefix="/generic",
    template_folder="../../templates/generic"
)

from . import routes
