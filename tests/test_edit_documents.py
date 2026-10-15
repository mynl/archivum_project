"""The Edit page's Documents panel ([edit-documents]); every write hits a temp library."""

from __future__ import annotations

import pandas as pd
import pytest

TZ = "Europe/London"
H1, H2, H3, H4 = ("aa11" + "0" * 60, "bb22" + "0" * 60, "bb23" + "0" * 60, "cc33" + "0" * 60)

REFS = [
    {"tag": "Book2020", "type": "book", "title": "A Book"},
    {"tag": "Chapter2020", "type": "incollection", "title": "A Chapter"},
    {"tag": "Lonely2021", "type": "article", "title": "Reference Only"},
]
DOCS = [
    {"hash": H1, "version": 0, "path": "a/book.pdf", "name": "book.pdf", "suffix": ".pdf",
     "size": 2_500_000, "create": pd.Timestamp("2022-02-02 10:00", tz=TZ)},
    {"hash": H2, "version": 0, "path": "a/scan.djvu", "name": "scan.djvu", "suffix": ".djvu",
     "size": 40_000, "create": pd.Timestamp("2021-01-01 10:00", tz=TZ)},
    {"hash": H3, "version": 0, "path": "a/other.pdf", "name": "other.pdf", "suffix": ".pdf",
     "size": 1_000, "create": pd.Timestamp("2020-01-01 10:00", tz=TZ)},
    {"hash": H4, "version": 0, "path": "a/loose.pdf", "name": "loose.pdf", "suffix": ".pdf",
     "size": 1_000, "create": pd.Timestamp("2020-01-01 10:00", tz=TZ)},
]
# the book and its chapter share H1; the book also has H2 as an alternate
LINKS = [
    {"tag": "Book2020", "hash": H1, "version": 0, "priority": 0},
    {"tag": "Book2020", "hash": H2, "version": 0, "priority": 1},
    {"tag": "Chapter2020", "hash": H1, "version": 0, "priority": 0},
]
READS = [{"hash": H2, "last_read": pd.Timestamp("2026-01-01"), "read_count": 3, "last_caller": "t"}]


@pytest.fixture()
def lib(tmp_library):
    return tmp_library(REFS, DOCS, LINKS, READS)


def _links(lib, tag):
    df = pd.read_feather(lib.config_path / "ref-doc.feather")
    return df[df.tag == tag].sort_values("priority")[["hash", "priority"]].values.tolist()


def test_linked_documents_assembles_the_panel(lib):
    docs = lib.linked_documents("Book2020")

    assert docs.hash.tolist() == [H1, H2]  # primary first
    assert docs.read_count.tolist() == [0, 3]
    # H1 is shared with the chapter; unlinking H2 would orphan it
    assert docs.shared_with.tolist() == [1, 0]
    assert lib.linked_documents("Lonely2021").empty


def test_resolve_hash_prefix(lib):
    assert lib.resolve_hash_prefix("AA1100") == (H1, 0)
    with pytest.raises(ValueError, match="at least 6"):
        lib.resolve_hash_prefix("aa11")
    with pytest.raises(ValueError, match="No document"):
        lib.resolve_hash_prefix("ffffff")


def test_resolve_hash_prefix_rejects_ambiguity(lib):
    with pytest.raises(ValueError, match="2 document hashes"):
        lib.resolve_hash_prefix("bb2", min_length=3)


@pytest.fixture()
def client(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    # no semantic warmup: its background model load would race the semantic
    # cache tests that run later in the same process
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_panel_renders_with_the_edit_form(client):
    html = client.get("/edit-tag/Book2020").get_data(as_text=True)

    assert 'id="edit-documents"' in html
    assert "Primary" in html and "Make primary" in html
    assert "/ingest?attach_to=Book2020" in html
    assert "becomes an orphan" in html  # H2 is linked nowhere else


def test_promote_unlink_and_link_routes(client, lib):
    html = client.post("/edit-tag/Book2020/documents/promote",
                       data={"hash": H2, "version": "0"}).get_data(as_text=True)
    assert "now the primary document" in html
    assert _links(lib, "Book2020") == [[H2, 0], [H1, 1]]

    client.post("/edit-tag/Book2020/documents/unlink", data={"hash": H1, "version": "0"})
    assert _links(lib, "Book2020") == [[H2, 0]]

    html = client.post("/edit-tag/Lonely2021/documents/link",
                       data={"hash_prefix": "cc3300"}).get_data(as_text=True)
    assert "as an alternate" in html
    assert _links(lib, "Lonely2021") == [[H4, 0]]

    html = client.post("/edit-tag/Lonely2021/documents/link",
                       data={"hash_prefix": "cc3300"}).get_data(as_text=True)
    assert "already linked" in html and "alert-danger" in html


def test_attach_to_offers_make_primary_in_the_workbench(client, tmp_path, monkeypatch):
    from io import BytesIO

    from archivum.web.routes import ingest as ingest_routes
    from archivum.web.services import ingest_batch as ib

    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")
    monkeypatch.setattr(ingest_routes, "_prepare", lambda path: {
        "hash": "abc123", "bibtex": "@article{raw, title = {A Paper}}", "candidates": {}, "error": None})

    page = client.get("/ingest?attach_to=Lonely2021").get_data(as_text=True)
    assert "Attaching a document to <strong>Lonely2021</strong>" in page
    assert 'name="attach_to" value="Lonely2021"' in page
    # an unknown tag is ignored
    assert "Attaching" not in client.get("/ingest?attach_to=Nope").get_data(as_text=True)

    workbench = client.post(
        "/ingest/start",
        data={"file": (BytesIO(b"%PDF-1.4\n"), "paper.pdf"), "attach_to": "Lonely2021"},
        content_type="multipart/form-data",
    ).get_data(as_text=True)
    assert "Make primary document of Lonely2021" in workbench
    assert 'name="match_tag" value="Lonely2021"' in workbench


def test_all_tags_includes_references_without_documents(lib):
    assert "Lonely2021" in lib.all_tags
