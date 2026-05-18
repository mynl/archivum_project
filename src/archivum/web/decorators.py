from functools import wraps

from flask import g


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not getattr(g, "is_admin", False):
            return "Permission Denied: Read-only mode enabled for external access.", 403
        return f(*args, **kwargs)

    return decorated_function
