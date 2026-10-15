"""The in-app PDF.js reader ([reader]); runs on a temp library with a real PDF."""

from __future__ import annotations

import threading

import pandas as pd
import pytest
from werkzeug.serving import make_server

from conftest import require_module

TZ = "Europe/London"
HPDF, HDJVU = "d1" + "0" * 62, "e2" + "0" * 62


def _make_pdf(path, pages=3):
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc.save(path)


@pytest.fixture()
def lib(tmp_library, tmp_path):
    refs = [{"tag": "Paper2024", "type": "article", "title": "{A Paper to Read}"},
            {"tag": "Scan1950", "type": "book", "title": "A Scan"}]
    docs = [{"hash": HPDF, "version": 0, "path": "d1/paper.pdf", "suffix": ".pdf",
             "create": pd.Timestamp("2024-01-01", tz=TZ)},
            {"hash": HDJVU, "version": 0, "path": "e2/scan.djvu", "suffix": ".djvu",
             "create": pd.Timestamp("2024-01-01", tz=TZ)}]
    links = [{"tag": "Paper2024", "hash": HPDF, "version": 0},
             {"tag": "Scan1950", "hash": HDJVU, "version": 0}]
    library = tmp_library(refs, docs, links)
    (library.doc_store_path / "d1").mkdir()
    (library.doc_store_path / "e2").mkdir()
    _make_pdf(library.doc_store_path / "d1" / "paper.pdf")
    (library.doc_store_path / "e2" / "scan.djvu").write_bytes(b"AT&TFORM fake djvu")
    return library


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


def _reads(lib, h):
    lib._read_df = pd.DataFrame()  # reload from disk
    match = lib.read_df[lib.read_df.hash == h]
    return 0 if match.empty else int(match.read_count.iloc[0])


def test_view_renders_the_reader_and_counts_one_read(app, lib):
    client = app.test_client()

    html = client.get("/view/Paper2024").get_data(as_text=True)

    assert "<title>Paper2024: A Paper to Read</title>" in html
    assert "vendor/pdfjs/web/viewer.html?file=/file/Paper2024" in html
    assert "webviewerloaded" in html
    assert _reads(lib, HPDF) == 1

    by_hash = client.get(f"/view-hash/{HPDF}").get_data(as_text=True)
    assert f"viewer.html?file=/file-hash/{HPDF}" in by_hash
    assert _reads(lib, HPDF) == 2


def test_file_serves_raw_bytes_with_ranges_and_no_read(app, lib):
    client = app.test_client()

    whole = client.get("/file/Paper2024")
    assert whole.status_code == 200
    assert whole.mimetype == "application/pdf"
    assert whole.get_data().startswith(b"%PDF")

    part = client.get(f"/file-hash/{HPDF}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.get_data()) == 100
    assert _reads(lib, HPDF) == 0


def test_non_pdf_view_still_sends_the_file(app, lib):
    response = app.test_client().get("/view/Scan1950")
    assert response.get_data().startswith(b"AT&T")
    assert _reads(lib, HDJVU) == 1


def test_vendored_viewer_is_served_as_javascript(app):
    from archivum.web.app import FRONTEND_VERSIONS

    response = app.test_client().get("/static/vendor/pdfjs/build/pdf.mjs")
    assert response.status_code == 200
    assert response.mimetype == "text/javascript"
    assert FRONTEND_VERSIONS["pdf.js"]


@pytest.mark.web
@pytest.mark.browser
def test_reader_opens_one_page_at_a_time(app, page):
    require_module("playwright")
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/view/Paper2024", wait_until="domcontentloaded")
        frame = page.locator("#reader").element_handle().content_frame()
        frame.wait_for_function(
            "() => window.PDFViewerApplication?.pdfViewer?.pagesCount > 0",
            timeout=60_000,
        )
        state = frame.evaluate(
            """() => ({
                scroll: PDFViewerApplication.pdfViewer.scrollMode,
                spread: PDFViewerApplication.pdfViewer.spreadMode,
                scale: PDFViewerApplication.pdfViewer.currentScaleValue,
                pages: PDFViewerApplication.pdfViewer.pagesCount,
            })"""
        )
        assert state == {"scroll": 3, "spread": 0, "scale": "page-fit", "pages": 3}
    finally:
        server.shutdown()
        thread.join(timeout=5)
