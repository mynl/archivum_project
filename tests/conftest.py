from __future__ import annotations

import importlib.util
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from werkzeug.serving import make_server


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


logger = logging.getLogger("archivum.tests")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow-web",
        action="store_true",
        default=False,
        help="Run slow Archivum web integration tests.",
    )
    parser.addoption(
        "--run-browser",
        action="store_true",
        default=False,
        help="Run Playwright browser smoke tests.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-slow-web"):
        skip_slow = pytest.mark.skip(reason="slow web tests require --run-slow-web")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)

    if not config.getoption("--run-browser"):
        skip_browser = pytest.mark.skip(reason="browser tests require --run-browser")
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip_browser)


def require_module(module_name: str) -> None:
    if importlib.util.find_spec(module_name) is None:
        pytest.skip(f"missing optional dependency: {module_name}")


@contextmanager
def logged_step(name: str, **fields: Any) -> Iterator[None]:
    started = time.perf_counter()
    details = " ".join(f"{key}={value!r}" for key, value in fields.items())
    logger.info("START %s %s", name, details)
    try:
        yield
    except Exception:
        logger.exception("FAIL %s after %.2fs", name, time.perf_counter() - started)
        raise
    else:
        logger.info("PASS %s in %.2fs", name, time.perf_counter() - started)


@pytest.fixture(scope="session")
def active_library():
    from archivum import DEFAULT_LIBRARY
    from archivum.cli import LibraryContext
    from archivum.library import Library

    lib_name = os.environ.get("ARCHIVUM_LIBRARY") or DEFAULT_LIBRARY
    if not lib_name:
        pytest.skip("No active Archivum library configured; set ARCHIVUM_LIBRARY or default_library.")

    with logged_step("load-active-library", library=lib_name):
        lib = Library(lib_name)
        LibraryContext.set(lib)
        if lib.is_empty:
            pytest.skip("Active Archivum library is empty.")
        logger.info(
            "Loaded library name=%s refs=%s docs=%s links=%s",
            lib.name,
            len(lib.ref_df),
            len(lib.doc_df),
            len(lib.ref_doc_df),
        )
        return lib


@pytest.fixture()
def app(active_library):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    LibraryContext.set(active_library)
    test_app = create_app()
    test_app.config.update(TESTING=True)
    return test_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def risk_measure_query() -> str:
    return "spectral risk measure"


@pytest.fixture(scope="session")
def risk_measure_q_query() -> str:
    return "q top 50 title ~ /spectral risk measure/"


@pytest.fixture(scope="session")
def risk_measure_rg_query() -> str:
    return "rg spectral risk measure"


@pytest.fixture(scope="session")
def sample_author(active_library) -> str:
    if "author" not in active_library.ref_df.columns:
        pytest.skip("Active library has no author column.")

    authors = (
        active_library.ref_df["author"]
        .dropna()
        .astype(str)
        .str.split(" and ")
        .explode()
        .str.strip()
    )
    authors = authors[(authors != "") & (authors.str.lower() != "nan")]
    if authors.empty:
        pytest.skip("Active library has no usable authors.")

    author = str(authors.value_counts().index[0])
    logger.info("Selected sample author: %s", author)
    return author


@pytest.fixture(scope="session")
def sample_hash_prefix(active_library) -> str:
    database = active_library.database
    if "hash" not in database.columns:
        pytest.skip("Active library database has no hash column.")

    hashes = database["hash"].dropna().astype(str)
    hashes = hashes[hashes.str.len() >= 2]
    if hashes.empty:
        pytest.skip("Active library has no usable hashes.")

    prefix = hashes.iloc[0][:2].upper()
    logger.info("Selected deterministic hash prefix: %s", prefix)
    return prefix


@pytest.fixture(scope="session")
def sample_tag(active_library) -> str:
    tags = active_library.all_tags
    if not tags:
        pytest.skip("Active library has no usable tags.")
    tag = str(tags[0])
    logger.info("Selected deterministic tag: %s", tag)
    return tag


def assert_ok_html(response, *, context: str) -> str:
    assert response.status_code == 200, f"{context}: expected HTTP 200, got {response.status_code}"
    html = response.get_data(as_text=True)
    lowered = html.lower()
    assert "no library open" not in lowered, f"{context}: library was not open"
    assert "traceback" not in lowered, f"{context}: traceback rendered"
    assert "error:" not in lowered, f"{context}: error marker rendered"
    logger.info("%s response status=%s bytes=%s", context, response.status_code, len(html))
    return html


def assert_json_ok(response, *, context: str) -> dict[str, Any]:
    assert response.status_code == 200, f"{context}: expected HTTP 200, got {response.status_code}"
    payload = response.get_json()
    assert isinstance(payload, dict), f"{context}: expected JSON object"
    assert "error" not in payload, f"{context}: route returned error: {payload.get('error')}"
    logger.info("%s json keys=%s", context, sorted(payload.keys()))
    return payload


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def live_server(app) -> Iterator[str]:
    port = find_free_port()
    server = make_server("127.0.0.1", port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    with logged_step("start-live-server", port=port):
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture()
def browser_page(page, live_server):
    page.set_default_timeout(120_000)
    return page


def log_payload_counts(name: str, payload: dict[str, Any]) -> None:
    logger.info(
        "%s papers=%s elements=%s nodes=%s edges=%s clusters=%s omitted=%s",
        name,
        payload.get("papers"),
        len(payload.get("elements") or []),
        len(payload.get("nodes") or []),
        len(payload.get("edges") or []),
        len(payload.get("clusters") or []),
        payload.get("omitted_count"),
    )


def make_tmp_library(tmp_path: Path, refs, docs=None, links=None, reads=None, **config):
    """
    A real ``Library`` whose feathers live under ``tmp_path``.

    For tests that must call ``save()``: the production library is never
    written. Built with ``Library.__new__`` because ``__init__`` resolves the
    configured library home. ``bibtex_file`` is empty, so no symlink is made.

    Parameters
    ----------
    refs, docs, links, reads : list of dict or DataFrame
        Rows for ref, doc, ref-doc, and read feathers; omitted ones are empty.
    **config
        Overrides for the ``Configurator`` fields.
    """
    import pandas as pd

    from archivum.config import Configurator
    from archivum.library import Library

    lib = Library.__new__(Library)
    lib.config_path = tmp_path / "library"
    lib.config_path.mkdir(parents=True)
    lib.doc_store_path = tmp_path / "docs"
    lib.doc_store_path.mkdir()
    lib.text_dir_path = tmp_path / "text"
    lib.text_dir_path.mkdir()
    lib.exports_dir_path = lib.config_path / "exports"
    lib.debug_dir_path = tmp_path / "debug"
    settings = dict(
        name="tmp", bibtex_file="", debug_mode=False, default_library="tmp",
        debug_dir=tmp_path / "debug", doc_store_lib="docs", full_text_lib="text",
        theme="system", ref_columns=["tag", "type", "author", "title", "year", "journal", "doi"],
        timezone="Europe/London",
    )
    settings.update(config)
    lib.config = Configurator(**settings)
    lib.needs_reload = False
    lib._ignore_until = 0.0
    lib._last_mtimes = {}
    lib._observer = None

    frames = {
        "ref.feather": (refs, ["tag", "type"]),
        "doc.feather": (docs, ["hash", "version", "path", "create"]),
        "ref-doc.feather": (links, ["tag", "hash", "version", "priority"]),
        "read.feather": (reads, ["hash", "last_read", "read_count", "last_caller"]),
    }
    # a missing priority is ranked by row order on load (assign_ref_doc_priority)
    for name, (rows, columns) in frames.items():
        df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [], columns=None if rows else columns)
        if not df.empty or name != "read.feather":
            df.to_feather(lib.config_path / name)
    lib.reset()
    return lib


@pytest.fixture()
def tmp_library(tmp_path):
    """Factory for :func:`make_tmp_library` bound to this test's ``tmp_path``."""
    return lambda *args, **kwargs: make_tmp_library(tmp_path, *args, **kwargs)
