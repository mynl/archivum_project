"""Search-box history filed on the server ([search-history]); never the production file."""

from __future__ import annotations

import csv
import threading
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
from werkzeug.serving import make_server

from conftest import require_module

# captured at import, before the autouse fixture in conftest.py patches it
from archivum.web.services.search_history import LIMIT, SearchHistory
from archivum.web.services.search_history import history_file as production_history_file

TZ = "Europe/London"
HPDF = "d1" + "0" * 62
BRIDGE = {"REMOTE_ADDR": "10.8.0.1"}


class Clock:
    """A settable clock for ``SearchHistory``."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def history(tmp_path, clock):
    return SearchHistory(tmp_path / "h.csv", clock=clock)


def _note(history, clock, box, *timed):
    for when, text in timed:
        clock.now = when
        history.note(box, text)


def _rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_a_burst_of_typing_files_its_final_text(history, clock, tmp_path):
    _note(history, clock, "query", (0, "ri"), (0.5, "risk m"), (1, "risk measure"))

    assert history.entries("query") == ["risk measure"]
    rows = _rows(tmp_path / "h.csv")
    assert [(r["box"], r["text"], r["uses"]) for r in rows] == [("query", "risk measure", "1")]


def test_a_quiet_gap_files_the_earlier_search(history, clock):
    _note(history, clock, "query", (0, "a"), (11, "b"))
    assert history.entries("query") == ["b", "a"]


def test_clearing_the_box_files_the_pending_search(history, clock):
    _note(history, clock, "query", (0, "a"), (1, ""), (2, "b"))
    assert history.entries("query") == ["b", "a"]


def test_a_repeated_search_moves_to_the_front_and_counts_its_uses(history, clock, tmp_path):
    _note(history, clock, "query", (0, "a"), (20, "b"), (40, "a"))

    assert history.entries("query") == ["a", "b"]
    uses = {r["text"]: r["uses"] for r in _rows(tmp_path / "h.csv")}
    assert uses == {"a": "2", "b": "1"}


def test_boxes_are_independent_and_a_new_instance_reloads(history, clock, tmp_path):
    _note(history, clock, "query", (0, "q text"))
    _note(history, clock, "ripgrep", (1, "rg text"))

    assert history.entries("query") == ["q text"]
    assert history.entries("ripgrep") == ["rg text"]
    assert history.entries("network") == []

    reloaded = SearchHistory(tmp_path / "h.csv", clock=clock)
    assert reloaded.entries("query") == ["q text"]
    assert reloaded.entries("ripgrep") == ["rg text"]


def test_history_is_capped_per_box(history, clock):
    _note(history, clock, "query", *((11.0 * i, f"search {i}") for i in range(LIMIT + 5)))

    entries = history.entries("query")
    assert len(entries) == LIMIT
    assert entries[0] == f"search {LIMIT + 4}"


@pytest.fixture()
def lib(tmp_library):
    refs = [{"tag": "Paper2024", "type": "article", "title": "{A Paper to Read}"}]
    docs = [{"hash": HPDF, "version": 0, "path": "d1/paper.pdf", "suffix": ".pdf",
             "create": pd.Timestamp("2024-01-01", tz=TZ)}]
    links = [{"tag": "Paper2024", "hash": HPDF, "version": 0}]
    return tmp_library(refs, docs, links)


@pytest.fixture()
def tmp_app(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    application = create_app()
    application.config.update(TESTING=True)
    return application


def test_search_route_notes_and_history_route_returns_it(tmp_app):
    client = tmp_app.test_client()

    client.get("/search", query_string={"q": "Paper"})
    assert client.get("/search-history/query").get_json() == ["Paper"]
    assert client.get("/search-history/nope").status_code == 404


def test_bridge_visitors_neither_record_nor_read_history(tmp_app):
    client = tmp_app.test_client()

    assert client.get("/search-history/query", environ_base=BRIDGE).status_code == 403
    client.get("/search", query_string={"q": "secret"}, environ_base=BRIDGE)
    client.get("/search", query_string={"q": ""}, environ_base=BRIDGE)
    assert "secret" not in client.get("/search-history/query").get_json()


@pytest.mark.web
@pytest.mark.route
@pytest.mark.active_library
def test_tests_never_touch_the_production_history(active_library, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    # no warmup threads: the shared client would start the real transformer load, which
    # then races the fakes in test_semantic_model_cache.py
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    LibraryContext.set(active_library)
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()

    path = production_history_file(active_library)
    before = path.stat().st_mtime_ns if path.exists() else None

    client.get("/search", query_string={"q": "spectral risk measure"})
    client.get("/search", query_string={"q": ""})

    assert client.get("/search-history/query").get_json() == ["spectral risk measure"]
    assert (path.stat().st_mtime_ns if path.exists() else None) == before


@pytest.mark.web
@pytest.mark.browser
def test_up_arrow_recalls_a_search_filed_by_clearing_the_box(tmp_app, page):
    require_module("playwright")
    server = make_server("127.0.0.1", 0, tmp_app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def is_search(text):
        def match(response):
            url = urlparse(response.url)
            return url.path == "/search" and parse_qs(url.query).get("q", [""])[0] == text
        return match

    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
        box = page.locator("#search-input")
        assert box.get_attribute("autocomplete") == "off"

        box.click()
        with page.expect_response(is_search("Paper")):
            box.type("Paper")
        page.locator("#results").get_by_text("Paper2024").first.wait_for()

        with page.expect_response(is_search("")):
            box.press("Control+a")
            box.press("Delete")

        with page.expect_response(is_search("Paper")):
            box.press("ArrowUp")
        assert box.input_value() == "Paper"
        page.locator("#results").get_by_text("Paper2024").first.wait_for()
    finally:
        server.shutdown()
        thread.join(timeout=5)
