"""
archivum project.
===================

"""
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



def _app_home() -> Path:
    """Return the archivum app home.

    ``~/.archivum`` by default; override with ``$ARCHIVUM_HOME``. The directory
    is not created here: the home is a set of symlinks into the data and
    settings trees (see scripts/New-ArchivumHome.ps1) and must exist before
    archivum runs. The override exists so a dev shell can point archivum at a
    scratch tree without disturbing the production home.
    """
    return Path(os.environ.get("ARCHIVUM_HOME", Path.home() / ".archivum")).expanduser()


# Core Paths
BASE_DIR = _app_home()
LIBRARIES_DIR = BASE_DIR / "libraries"
GLOBAL_CONFIG_PATH = BASE_DIR / "global-config.yaml"

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
    """Load the global config, merged over the defaults.

    The home is never populated here. A missing file means the app home was not
    wired up (see scripts/New-ArchivumHome.ps1), and silently writing defaults
    would plant a real file at what should be a symlink site.
    """
    if not GLOBAL_CONFIG_PATH.exists():
        # Legacy name from before the hyphenated form.
        old_config = BASE_DIR / "global_config.yaml"
        if old_config.exists():
            old_config.rename(GLOBAL_CONFIG_PATH)
        else:
            raise FileNotFoundError(
                f"archivum global config not found at {GLOBAL_CONFIG_PATH}. "
                "Set ARCHIVUM_HOME or build the app home with "
                "scripts/New-ArchivumHome.ps1 before running archivum."
            )

    with open(GLOBAL_CONFIG_PATH, "r") as f:
        # Merge with defaults to ensure new keys exist after updates
        config = yaml.safe_load(f) or {}
        return {**DEFAULT_GLOBAL_CONFIG, **config}


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
