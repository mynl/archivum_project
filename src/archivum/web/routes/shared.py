from flask import Blueprint, render_template, request, send_file, abort, Response, stream_with_context, url_for
from ...cli import LibraryContext
from ...utilities import trim_author, clean_latex
from ...analytics.semantic import analyze_semantic
from ...search.query import normalize_query
from ...search.universe import resolve_universe
from ..cache import (
    get_cached_data,
    get_hash_meta_cache,
    get_rg_cache_item,
    invalidate_cached_data,
    set_rg_cache_item,
)
from ..decorators import admin_required
from ..presenters.search import prepare_search_results
import pandas as pd
from pathlib import Path
import logging
import json
import html
import mimetypes
import subprocess
import os
import time
import re
from datetime import datetime
from collections import Counter

logger = logging.getLogger(__name__)

bp = Blueprint('main', __name__)

@bp.before_app_request
def check_for_reload():
    """Check if the library needs a reload due to external changes."""
    lib = LibraryContext.get()
    if not lib.is_empty and lib.needs_reload:
        logger.info(f"External changes detected. Reloading library '{lib.name}' in web context.")
        lib.reset()
        LibraryContext.refresh()

def _render_export_button(id_prefix, hashes, input_id='rg-input'):
    """Render the active export button as an HTMX out-of-band swap."""
    return render_template(
        "components/export_button_active.html",
        id_prefix=id_prefix,
        hashes=hashes,
        input_id=input_id,
    )


AUTHOR_VIEW_MODES = [
    ('list', 'Dense List'),
    ('verbose', 'Verbose'),
    ('table', 'Table'),
]


def render_search_results(df, view_mode='list'):
    """Render search results consistently across query and author views."""
    prepared_df = prepare_search_results(df)
    return render_template('components/results.html', results=prepared_df, view_mode=view_mode)


def author_query_results(lib, author):
    query_expr = f"select year, path, hash, type, * ! /{author}/ order by -year"
    result = lib.database.querex(query_expr)
    if not isinstance(result, pd.DataFrame):
        raise ValueError(f"Query error: {result}")
    return result


def render_author_results_parts(lib, author, view_mode='list', *, oob_header=True):
    result = author_query_results(lib, author)
    header = render_template(
        'components/author_results_header.html',
        author=author,
        view_mode=view_mode,
        view_modes=AUTHOR_VIEW_MODES,
        oob_header=oob_header,
    )
    return header, render_search_results(result, view_mode=view_mode)
