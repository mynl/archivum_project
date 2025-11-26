"""
archivum project.
===================

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
from pathlib import Path

__appname__ = 'archivum'
__author__ = 'Stephen J. Mildenhall'

__version__ = "0.8.0"
__date__ = '2025-11-26'


# def _get_local_folder():
#     local_app_data = Path(os.environ["LOCALAPPDATA"])
#     my_app_data = local_app_data / __appname__
#     # print(my_app_data)
#     assert my_app_data.exists(), 'Application database does not exist.'
#     # my_app_data.mkdir(parents=True, exist_ok=True)
#     return my_app_data


def _get_local_folder():
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    my_app_data = base / __appname__
    if not my_app_data.exists():
        my_app_data.mkdir(parents=True, exist_ok=True)
        # raise FileNotFoundError("Application database does not exist.")
    return my_app_data

# e.g. /users/steve/appdata/local/archivum
BASE_DIR = _get_local_folder()

# TODO NAUGHTY
DEBUG_DIR = Path('\\tmp\\archivum_debug')
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

APP_NAME = __appname__

# TODO NAUGHTY
DEFAULT_CONFIG_FILE = "uber-library"

EMPTY_LIBRARY = type('EmptyLibrary', (), {'name': 'No library open', 'is_empty': True})

# avoid circular import errors, import here
from . library import Library  # noqa
