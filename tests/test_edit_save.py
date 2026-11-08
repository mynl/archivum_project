"""Saving from Edit reports in a status line beside the buttons; writes hit a temp library."""

from __future__ import annotations

import threading

import pandas as pd
import pytest
from werkzeug.serving import make_server

from conftest import require_module

TZ = "Europe/London"
H1 = "a1" + "0" * 62

REFS = [
    {"tag": "Keep2020", "type": "article", "title": "One Work", "author": "Doe, Jane", "year": "2020",
     "journal": "J. One", "doi": "10.1/one", "arc-source": "mendeley"},
    {"tag": "Other2019", "type": "book", "title": "Other", "author": "Roe, R.", "year": "2019",
     "journal": "", "doi": "", "arc-source": "mendeley"},
]
DOCS = [{"hash": H1, "version": 0, "path": "a1/one.pdf", "name": "one.pdf", "suffix": ".pdf",
         "size": 1000, "create": pd.Timestamp("2020-01-01", tz=TZ)}]
LINKS = [{"tag": "Keep2020", "hash": H1, "version": 0, "priority": 0}]


@pytest.fixture()
def lib(tmp_library):
    return tmp_library(REFS, DOCS, LINKS)


@pytest.fixture()
def app(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    application = create_app()
    application.config.update(TESTING=True)
    return application


def _refs(lib):
    return pd.read_feather(lib.config_path / "ref.feather").set_index("tag")


def _entry(tag, title, type_="article"):
    return f"@{type_}{{{tag},\n  author = {{Doe, Jane}},\n  title = {{{title}}},\n  year = {{2020}},\n}}"


def test_save_reloads_the_editor_with_a_status_line(app, lib):
    response = app.test_client().post("/edit-tag/Keep2020", data={"bibtex": _entry("Keep2020", "One Work, Revised")})
    html = response.get_data(as_text=True)

    assert response.status_code == 200 and "HX-Retarget" not in response.headers
    assert 'id="edit-form"' in html and "Reference Updated" not in html
    assert "<strong>Keep2020</strong> updated" in html and 'data-autoclose="20000"' in html
    assert 'href="/view/Keep2020"' in html
    assert _refs(lib).loc["Keep2020", "title"] == "One Work, Revised"


def test_rename_reports_and_updates_the_sidebar(app, lib):
    html = app.test_client().post(
        "/edit-tag/Other2019", data={"bibtex": _entry("Renamed2019", "Other", "book")}).get_data(as_text=True)

    assert "<strong>Renamed2019</strong> updated, renamed from Other2019" in html
    assert "/view/Renamed2019" not in html  # no document, no View link
    assert '"other2019"' in html and '"/edit-tag/Renamed2019"' in html  # sidebar script
    assert "Renamed2019" in _refs(lib).index and "Other2019" not in _refs(lib).index
    assert _refs(lib).loc["Renamed2019", "arc-source"] == "mendeley"


def test_save_deletes_fields_dropped_from_the_box(app, lib):
    # the box holds no journal and no doi, as if both lines were deleted
    html = app.test_client().post(
        "/edit-tag/Keep2020", data={"bibtex": _entry("Keep2020", "One Work")}).get_data(as_text=True)

    refs = _refs(lib)
    assert refs.loc["Keep2020", "journal"] == "" and refs.loc["Keep2020", "doi"] == ""
    assert "J. One" not in html and "10.1/one" not in html
    # never shown in the box, so never deleted from it
    assert refs.loc["Keep2020", "arc-source"] == "mendeley"


def test_save_warns_of_fields_outside_ref_columns(app, lib):
    bibtex = "@article{Keep2020,\n  title = {One Work},\n  DOI = {10.1/two},\n  note = {Read later},\n}"
    html = app.test_client().post("/edit-tag/Keep2020", data={"bibtex": bibtex}).get_data(as_text=True)

    assert "<strong>Keep2020</strong> updated; not saved: note (not in ref_columns)" in html
    assert "alert-warning" in html and "data-autoclose" not in html
    refs = _refs(lib)
    assert refs.loc["Keep2020", "doi"] == "10.1/two"  # field names ignore case
    assert refs.loc["Keep2020", "author"] == "" and "note" not in refs.columns


@pytest.mark.parametrize("bibtex, reason", [
    (_entry("Other2019", "One Work"), "already exists"),
    ("", "cannot be empty"),
    (_entry("Keep2020", "One Work").replace("{2020}", "2021"), "Could not read year; nothing was saved"),
])
def test_refused_save_lands_in_the_status_slot(app, lib, bibtex, reason):
    response = app.test_client().post("/edit-tag/Keep2020", data={"bibtex": bibtex})
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#edit-status"
    assert reason in html and "alert-danger" in html and "data-autoclose" not in html
    assert _refs(lib).loc["Keep2020", "title"] == "One Work"


@pytest.mark.web
@pytest.mark.browser
def test_ctrl_s_saves_and_the_status_line_is_scheduled_to_close(app, lib, page):
    require_module("playwright")
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/edit?tag=Keep2020", wait_until="domcontentloaded")
        page.locator("#bibtex").fill(_entry("Keep2020", "One Work, Revised"))
        page.keyboard.press("Control+s")

        status = page.locator("#edit-status .alert-success")
        status.wait_for(timeout=10_000)
        assert "Keep2020 updated" in status.inner_text()
        # edit.html took the close-after delay off the element when it scheduled it
        assert status.get_attribute("data-autoclose") is None
        assert _refs(lib).loc["Keep2020", "title"] == "One Work, Revised"

        # a refused save keeps the edit and shows the reason instead
        page.locator("#bibtex").fill(_entry("Other2019", "One Work"))
        page.keyboard.press("Control+s")
        page.locator("#edit-status .alert-danger").wait_for(timeout=10_000)
        assert "already exists" in page.locator("#edit-status").inner_text()
        assert "Other2019" in page.locator("#bibtex").input_value()
    finally:
        server.shutdown()
        thread.join(timeout=5)
