from pathlib import Path

class Config:
    SECRET_KEY = 'a_very_secret_key_that_should_be_changed_in_production'
    PDF_DIR = Path("/s/telos/python/archivum_project/pending-papers")
    LIBRARY_PATH = '/s/appdata/archivum/uber-library.archivum-feather'
