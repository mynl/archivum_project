# noqa
from flask import Flask

from .config import Config
from .blueprints.new_doc import new_doc_bp
from .blueprints.querex import querex_bp
from .blueprints.grid import grid_bp
from .blueprints.generic import generic_bp
from .blueprints.home import home_bp

# Import necessary archivum components here, or consider passing them
# to blueprints if they are truly application-wide services.
# For now, let's keep them here as they are needed by multiple blueprints
# or initialize them within relevant blueprint services.
import archivum.library as arcl


def create_app():
    app = Flask(__name__)

    # load config from the Config class
    app.config.from_object(Config)

    # Initialize library once for the application
    # and attach to app object
    app.lib = arcl.Library('uber-library')

    # Register Blueprints
    app.register_blueprint(new_doc_bp)
    app.register_blueprint(querex_bp)
    app.register_blueprint(grid_bp)
    app.register_blueprint(generic_bp)
    app.register_blueprint(home_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host='0.0.0.0', debug=True, port=9777, use_reloader=True)
