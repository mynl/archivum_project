"""
archivum project.
===================

v 1.0.0
    Alpha release
    Uses querexfuzz with config files for each table; deleted unneeded files
    Added hashing capability
    Added Library.history

v 0.9.0
    New file layout

v 0.8.0
    LibraryBase
    Work on import_bibtex
    Creator for Config class

v 0.7.0
    moved to Library having a qd function in place of older fGT

v 0.6.0
    added self discovery for great2 uber uber
    updated stand alone uber
v 0.5.0

"""
import sys
import os
import yaml
from pathlib import Path

__appname__ = "archivum"
__author__ = "Stephen J. Mildenhall"
__version__ = "1.0.0"
__date__ = "2025-03-06"


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
GLOBAL_CONFIG_PATH = BASE_DIR / "global_config.yaml"

# Ensure Core Dirs Exist
LIBRARIES_DIR.mkdir(exist_ok=True)

# Default Configuration
DEFAULT_GLOBAL_CONFIG = {
    "default_library": None,
    "theme": "system",
    "debug_mode": False,
    "debug_dir": "",
    "doc_store_lib": "docs",
    "version": __version__,
}


def _load_global_config() -> dict:
    """Loads global config or creates it with defaults if missing."""
    if not GLOBAL_CONFIG_PATH.exists():
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

DEBUG_DIR = Path(GLOBAL_CONFIG["debug_dir"])
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LIBRARY = GLOBAL_CONFIG["default_library"]

EMPTY_LIBRARY = type("EmptyLibrary", (), {"name": "No library open", "is_empty": True})

# avoid circular import errors, import here
# from . library import Library  # noqa
