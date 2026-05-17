"""
archivum project.
===================

"""
import sys
import os
from importlib.metadata import PackageNotFoundError, version
import yaml
from pathlib import Path

__appname__ = "archivum"
__author__ = "Stephen J. Mildenhall"

# pyproject.toml owns the package version; this mirrors installed metadata.
try:
    __version__ = version(__appname__)
except PackageNotFoundError:
    __version__ = "0+unknown"



def _get_local_folder() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    app_data = base / __appname__
    if not app_data.exists():
        app_data.mkdir(parents=True, exist_ok=True)
    return app_data


# Core Paths
BASE_DIR = _get_local_folder()
LIBRARIES_DIR = BASE_DIR / "libraries"
GLOBAL_CONFIG_PATH = BASE_DIR / "global-config.yaml"

# Ensure Core Dirs Exist
LIBRARIES_DIR.mkdir(exist_ok=True)

# Default Configuration
DEFAULT_GLOBAL_CONFIG = {
    "default_library": None,
    "theme": "system",
    "debug_mode": False,
    "debug_dir": "debug",
    "doc_store_lib": "docs",
    "full_text_lib": "full-text",
    "editor_command": "subl",
    "pdf_viewer_command": None,  # None means use system default (os.startfile)
}


def _load_global_config() -> dict:
    """Loads global config or creates it with defaults if missing."""
    if not GLOBAL_CONFIG_PATH.exists():
        # Check if old config exists and rename it
        old_config = BASE_DIR / "global_config.yaml"
        if old_config.exists():
            old_config.rename(GLOBAL_CONFIG_PATH)
        else:
            try:
                with open(GLOBAL_CONFIG_PATH, "w") as f:
                    yaml.dump(DEFAULT_GLOBAL_CONFIG, f, default_flow_style=False)
                return DEFAULT_GLOBAL_CONFIG.copy()
            except OSError as e:
                print(f"Warning: Could not create config file: {e}", file=sys.stderr)
                return DEFAULT_GLOBAL_CONFIG.copy()

    try:
        with open(GLOBAL_CONFIG_PATH, "r") as f:
            # Merge with defaults to ensure new keys exist after updates
            config = yaml.safe_load(f) or {}
            return {**DEFAULT_GLOBAL_CONFIG, **config}
    except Exception as e:
        print(f"Error loading global config: {e}", file=sys.stderr)
        return DEFAULT_GLOBAL_CONFIG.copy()


# Initialize Global Config
GLOBAL_CONFIG = _load_global_config()

def resolve_path(p: str) -> Path:
    """Resolves a path relative to BASE_DIR if it's not absolute or root-relative."""
    path = Path(p)
    # On Windows, paths starting with \ are root-relative and have an anchor but aren't 'absolute'
    if path.is_absolute() or path.anchor in ('\\', '/'):
        return path
    return BASE_DIR / path


DEFAULT_LIBRARY = GLOBAL_CONFIG["default_library"]

EMPTY_LIBRARY = type("EmptyLibrary", (), {"name": "No library open", "is_empty": True})

# avoid circular import errors, import here
# from . library import Library  # noqa
