"""archivum project."""

import os
from pathlib import Path


__appname__ = 'archivum'
__author__ = 'Stephen Mildenhall'
__date__ = '2025-05-22'


def _get_local_folder():
    local_app_data = Path(os.environ["LOCALAPPDATA"])
    my_app_data = local_app_data / __appname__
    assert my_app_data.exists(), 'Application database does not exist.'
    # my_app_data.mkdir(parents=True, exist_ok=True)
    return my_app_data


BASE_DIR = _get_local_folder()
APP_SUFFIX = '.archivum-config'
DEFAULT_CONFIG_FILE = BASE_DIR / "default" + APP_SUFFIX

# avoid circular import errors, import here
from . library import Library  # noqa

