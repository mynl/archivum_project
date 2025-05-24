"""archivum project."""

from . __version__ import __version__
import sys
import os
from pathlib import Path


__appname__ = 'archivum'
__author__ = 'Stephen Mildenhall'
__date__ = '2025-05-22'


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


BASE_DIR = _get_local_folder()
# for imports
(BASE_DIR / 'imports').mkdir(exist_ok=True)

APP_NAME = __appname__
APP_SUFFIX = '.archivum-config'
DEFAULT_CONFIG_FILE = BASE_DIR / f"default{APP_SUFFIX}"

# BIBTEX_DIR = "\\s\\telos\\biblio\\"


# avoid circular import errors, import here
from . library import Library  # noqa
