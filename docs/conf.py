"""Sphinx configuration for Archivum."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import archivum

project = "Archivum"
copyright = "2026, Stephen J Mildenhall"
author = "Stephen J Mildenhall"
release = archivum.__version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.githubpages",
    "sphinx.ext.mathjax",
    "sphinx.ext.viewcode",
    "sphinx_click",
    "sphinx_copybutton",
    "sphinx_rtd_theme",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
master_doc = "index"

autosectionlabel_prefix_document = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"

pygments_style = "sphinx"

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "prev_next_buttons_location": "both",
    "sticky_navigation": True,
}
html_logo = "_static/archivum.png"
html_favicon = "_static/favicon.ico"
html_static_path = ["_static"]
html_title = f"Archivum {release}"
