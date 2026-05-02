from flask import Flask
from ..cli import LibraryContext

def create_app():
    app = Flask(__name__)
    
    # Import routes after app is created to avoid circular imports
    from . import routes
    app.register_blueprint(routes.bp)
    
    @app.context_processor
    def inject_lib():
        return dict(lib=LibraryContext.get())
        
    return app
