from flask import Flask
from ..cli import LibraryContext

def create_app():
    app = Flask(__name__)
    
    # Import routes after app is created to avoid circular imports
    from . import routes
    app.register_blueprint(routes.bp)
    
    @app.before_request
    def set_admin_status():
        from flask import request, g
        # We strictly check the IMMEDIATE caller. 
        # If it's the VPS bridge (10.8.0.1), it's a guest.
        # If it's 10.8.0.2 (VPN) or local, it's Admin.
        remote_ip = request.remote_addr
        g.is_admin = remote_ip != '10.8.0.1'

    @app.context_processor
    def inject_lib():
        from flask import g
        return dict(lib=LibraryContext.get(), is_admin=getattr(g, 'is_admin', False))
        
    return app
