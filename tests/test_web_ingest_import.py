from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from archivum.library import Library, LibraryImportBlocked
from archivum.web.services import ingest_batch as ib


def _fake_library(tmp_path):
    lib = Library.__new__(Library)
    lib.config_path = tmp_path / "library"
    lib.config_path.mkdir()
    lib.text_dir_path = tmp_path / "text"
    lib.text_dir_path.mkdir()
    lib.config = SimpleNamespace(extractor="noop", hash_workers=1)
    return lib


def test_import_staged_document_injects_file_and_runs_importer(tmp_path, monkeypatch):
    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    lib = _fake_library(tmp_path)
    calls = {}

    class FakeImporter:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs
            calls["bibtex"] = kwargs["bibtex_file_path"].read_text(encoding="utf-8")
            self.ported_df = pd.DataFrame([{"tag": "smith2024"}])
            self.ref_df = pd.DataFrame([{"tag": "smith2024", "type": "article", "title": "A Paper"}])
            self.doc_df = pd.DataFrame(
                [{"path": str(staged), "hash": "abc123", "version": 0}]
            )

        def import_bibtex_file(self):
            calls["import_bibtex_file"] = True
            return pd.DataFrame()

        def import_analysis(self):
            return pd.DataFrame([{"tag": "smith2024", "action": "Import", "title": "A Paper"}])

        def update_library(self, save=True):
            calls["update_library_save"] = save

    monkeypatch.setattr("archivum.import_bibtex.Bib2df_Incremental", FakeImporter)

    importer = lib.import_staged_document(
        """
        @article{smith2024,
          author = {Smith, J.},
          title = {A Paper},
          year = {2024},
        }
        """,
        staged,
        extract_text=False,
    )

    assert importer.ported_df.iloc[0].tag == "smith2024"
    assert calls["import_bibtex_file"] is True
    assert calls["update_library_save"] is True
    assert calls["kwargs"]["reference_library"] is lib
    assert calls["kwargs"]["doc_dir"] == staged.parent.resolve()
    assert calls["kwargs"]["add_hashes"] is True
    assert calls["kwargs"]["incremental"] is True
    file_line = next(line for line in calls["bibtex"].splitlines() if "file" in line)
    assert f":{staged.resolve().as_posix()}:pdf" in file_line
    assert "\\_" not in file_line


def test_preview_staged_document_returns_importer_bibtex_without_update(tmp_path, monkeypatch):
    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    lib = _fake_library(tmp_path)
    calls = {}

    class FakeImporter:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs
            self.ported_df = pd.DataFrame([{"tag": "Smith2024"}])
            self.ref_df = pd.DataFrame(
                [{"tag": "Smith2024", "type": "article", "author": "Smith, John", "title": "A Paper", "year": "2024"}]
            )
            self.doc_df = pd.DataFrame(
                [{"path": str(staged), "hash": "abc123", "version": 0}]
            )

        def import_bibtex_file(self):
            calls["import_bibtex_file"] = True
            return pd.DataFrame()

        def import_analysis(self):
            return pd.DataFrame([{"tag": "Smith2024", "action": "Import", "title": "A Paper"}])

        def update_library(self, save=True):
            raise AssertionError("preview must not update the library")

    monkeypatch.setattr("archivum.import_bibtex.Bib2df_Incremental", FakeImporter)

    preview = lib.preview_staged_document_import(
        "@article{rawkey, title = {A Paper}, year = {2024}}",
        staged,
    )

    assert preview["tag"] == "Smith2024"
    assert "Smith2024" in preview["bibtex"]
    assert "Smith, John" in preview["bibtex"]
    assert preview["blocked"] is False
    assert calls["import_bibtex_file"] is True
    assert calls["kwargs"]["write_audit"] is False


def test_import_staged_document_blocks_duplicate_analysis(tmp_path, monkeypatch):
    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    lib = _fake_library(tmp_path)

    class FakeImporter:
        ported_df = pd.DataFrame([{"tag": "smith2024"}])
        doc_df = pd.DataFrame()

        def __init__(self, **kwargs):
            pass

        def import_bibtex_file(self):
            return pd.DataFrame()

        def import_analysis(self):
            return pd.DataFrame(
                [{"tag": "smith2024", "action": "Merge/Warn", "title": "A Paper"}]
            )

        def update_library(self, save=True):
            raise AssertionError("blocked imports must not update the library")

    monkeypatch.setattr("archivum.import_bibtex.Bib2df_Incremental", FakeImporter)

    with pytest.raises(LibraryImportBlocked, match="same DOI or title"):
        lib.import_staged_document(
            "@article{smith2024, title = {A Paper}, year = {2024}}",
            staged,
            extract_text=False,
        )


def test_import_staged_document_allow_duplicate_keeps_the_entry(tmp_path, monkeypatch):
    """Import anyway: no conflict check, and the importer keeps SKIP (Dupe) rows."""
    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    lib = _fake_library(tmp_path)
    calls = {}

    class FakeImporter:
        ported_df = pd.DataFrame([{"tag": "smith2024a"}])
        doc_df = pd.DataFrame()

        def __init__(self, **kwargs):
            calls["skip_duplicates"] = kwargs["skip_duplicates"]

        def import_bibtex_file(self):
            return pd.DataFrame()

        def import_analysis(self):
            raise AssertionError("Import anyway must not consult the analysis")

        def update_library(self, save=True):
            calls["update_library_save"] = save

    monkeypatch.setattr("archivum.import_bibtex.Bib2df_Incremental", FakeImporter)

    lib.import_staged_document(
        "@article{smith2024, title = {A Paper}, year = {2024}}",
        staged,
        extract_text=False,
        allow_duplicate=True,
    )

    assert calls["skip_duplicates"] is False
    assert calls["update_library_save"] is True


def test_import_staged_document_writes_no_audit_files(tmp_path, monkeypatch):
    """Staged imports must leave nothing behind in debug_dir or import-audit."""
    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    lib = _fake_library(tmp_path)
    seen = {}

    class FakeImporter:
        def __init__(self, **kwargs):
            seen["write_audit"] = kwargs["write_audit"]
            self.ported_df = pd.DataFrame([{"tag": "smith2024"}])
            self.ref_df = pd.DataFrame([{"tag": "smith2024"}])
            self.doc_df = pd.DataFrame([{"path": str(staged), "hash": "abc", "version": 0}])

        def import_bibtex_file(self):
            return pd.DataFrame()

        def import_analysis(self):
            return pd.DataFrame([{"tag": "smith2024", "action": "Import", "title": "A"}])

        def update_library(self, save=True):
            pass

    monkeypatch.setattr("archivum.import_bibtex.Bib2df_Incremental", FakeImporter)

    lib.import_staged_document(
        "@article{smith2024, title = {A Paper}, year = {2024}}",
        staged,
        extract_text=False,
    )

    assert seen["write_audit"] is False
    assert not (lib.config_path / "import-audit").exists()
    # the review bib is staged beside the document, not in the library
    assert not (lib.config_path / "staging").exists()
    assert (staged.parent / "web-ingest.bib").exists()


def _batch_app(monkeypatch, fake_library, prepared=None):
    """App wired to a fake library, with metadata discovery stubbed out."""
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app
    from archivum.web.routes import ingest as ingest_routes

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: fake_library))
    monkeypatch.setattr(
        ingest_routes,
        "_prepare",
        lambda path: prepared
        or {"hash": "abc123", "bibtex": "@article{raw, title = {A Paper}}",
            "candidates": {}, "error": None},
    )
    app = create_app()
    app.config.update(TESTING=True)
    return app


def _stage_one(client, name="paper.pdf"):
    """Post a single document to /ingest/start and return the response HTML."""
    from io import BytesIO

    return client.post(
        "/ingest/start",
        data={"file": (BytesIO(b"%PDF-1.4\n"), name)},
        content_type="multipart/form-data",
    )


class _FakePreviewLibrary:
    is_empty = False
    needs_reload = False
    ref_df = pd.DataFrame({"tag": []})
    doc_df = pd.DataFrame({"hash": [], "version": []})
    ref_doc_df = pd.DataFrame({"tag": [], "hash": [], "version": []})

    def __init__(self):
        self.calls = {}

    def preview_staged_document_import(self, bibtex, staged_document_path, **kwargs):
        self.calls["preview_path"] = staged_document_path
        self.calls["preview_kwargs"] = kwargs
        return {
            "bibtex": "@article{Smith2024,\n  author = {Smith, John},\n}",
            "tag": "Smith2024",
            "analysis": pd.DataFrame([{"tag": "Smith2024", "action": "Import"}]),
            "blocked": False,
            "blocked_message": "",
            "conflict": None,
        }

    def reset(self):
        raise AssertionError("test library should not reset")


def test_ingest_start_stages_document_and_renders_workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")
    lib = _FakePreviewLibrary()
    app = _batch_app(monkeypatch, lib)

    response = _stage_one(app.test_client())
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Ingest Workbench" in html
    assert "Final Tag: Smith2024" in html
    # each document is staged in its own directory so the importer scans one file
    staged_path = lib.calls["preview_path"]
    assert staged_path.name == "paper.pdf"
    assert list(staged_path.parent.iterdir()) == [staged_path]


def test_ingest_commit_delegates_to_library_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def import_staged_document(self, bibtex, staged_document_path, **kwargs):
            self.calls["bibtex"] = bibtex
            self.calls["path"] = staged_document_path
            self.calls["kwargs"] = kwargs
            return SimpleNamespace(ref_df=pd.DataFrame([{"tag": "Smith2024"}]))

    lib = FakeLibrary()
    app = _batch_app(monkeypatch, lib)
    client = app.test_client()

    start = _stage_one(client).get_data(as_text=True)
    batch_id = start.split('name="batch_id" value="')[1].split('"')[0]

    response = client.post(
        "/ingest/commit",
        data={
            "bibtex": "@article{smith2024,\n  title = {A Paper},\n}",
            "batch_id": batch_id,
            "idx": "0",
            "hash": "abc123",
        },
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "archived as" in html
    assert "Smith2024" in html
    # the banner is an out-of-band append, so the page stays on ingest
    assert 'hx-swap-oob="beforeend:#ingest-results"' in html
    assert "Zone A: The Documents" in html
    assert lib.calls["kwargs"]["known_hash"] == "abc123"
    assert lib.calls["kwargs"]["source_label"] == "web-ingest"
    assert lib.calls["kwargs"]["allow_duplicate"] is False


def test_ingest_commit_import_anyway_allows_the_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def import_staged_document(self, bibtex, staged_document_path, **kwargs):
            self.calls["kwargs"] = kwargs
            return SimpleNamespace(ref_df=pd.DataFrame([{"tag": "Smith2024a"}]))

    lib = FakeLibrary()
    client = _batch_app(monkeypatch, lib).test_client()
    start = _stage_one(client).get_data(as_text=True)
    batch_id = start.split('name="batch_id" value="')[1].split('"')[0]

    html = client.post(
        "/ingest/commit",
        data={"bibtex": "@article{s, title={A}}", "batch_id": batch_id,
              "idx": "0", "hash": "abc123", "import_anyway": "on"},
    ).get_data(as_text=True)

    assert lib.calls["kwargs"]["allow_duplicate"] is True
    assert "archived as" in html and "Smith2024a" in html


def test_ingest_commit_on_a_match_stays_on_the_document(tmp_path, monkeypatch):
    """GO on an unconfirmed match keeps the document and the edited BibTeX."""
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def import_staged_document(self, *args, **kwargs):
            raise LibraryImportBlocked("smith2024 has the same file and title as Old2020.")

    lib = FakeLibrary()
    app = _batch_app(monkeypatch, lib)
    client = app.test_client()

    start = _stage_one(client).get_data(as_text=True)
    batch_id = start.split('name="batch_id" value="')[1].split('"')[0]

    response = client.post(
        "/ingest/commit",
        data={"bibtex": "@article{s, title={Edited Title}}", "batch_id": batch_id,
              "idx": "0", "hash": "abc123"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Ingest Workbench" in html
    assert "title={Edited Title}" in html
    assert "not imported" in html and "same file and title as Old2020" in html
    assert ib.load_batch(batch_id)["items"][0]["status"] == "pending"


def test_ingest_preview_route_uses_library_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")
    lib = _FakePreviewLibrary()
    app = _batch_app(monkeypatch, lib)
    client = app.test_client()

    start = _stage_one(client).get_data(as_text=True)
    batch_id = start.split('name="batch_id" value="')[1].split('"')[0]

    response = client.post(
        "/ingest/preview",
        data={
            "bibtex": "@article{rawkey,\n  title = {A Paper},\n}",
            "batch_id": batch_id,
            "idx": "0",
            "hash": "abc123",
        },
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Final Tag: Smith2024" in html
    assert "Smith, John" in html
    assert lib.calls["preview_kwargs"]["known_hash"] == "abc123"


def test_ingest_offers_replace_for_resolvable_duplicate(tmp_path, monkeypatch):
    """Merge/Warn must offer the swap, not dead-end with a red banner."""
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def preview_staged_document_import(self, bibtex, path, **kwargs):
            return {
                "bibtex": "@article{New2024}",
                "tag": "New2024",
                "analysis": pd.DataFrame(
                    [{"tag": "New2024", "action": "Merge/Warn", "match tag": "Old2020"}]
                ),
                "blocked": True,
                "blocked_message": "New2024 has the same title as Old2020.",
                "conflict": {
                    "action": "Merge/Warn", "tag": "New2024", "match_tag": "Old2020",
                    "title": "A Paper", "same_file": False, "matched_on": "title",
                    "level": "warning", "match_has_doc": True, "resolvable": True,
                    "message": "New2024 has the same title as Old2020.",
                },
            }

    app = _batch_app(monkeypatch, FakeLibrary())
    html = _stage_one(app.test_client()).get_data(as_text=True)

    assert "alert-warning" in html
    assert "Same title" in html
    assert "Replace document on Old2020" in html
    assert "Also update the reference metadata" in html
    assert "/view/Old2020" in html
    assert "Import anyway as New2024" in html
    assert 'name="import_anyway" value="on"' in html


def test_ingest_same_file_warns_red_with_import_anyway(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def preview_staged_document_import(self, bibtex, path, **kwargs):
            conflict = {
                "action": "SKIP (Dupe)", "tag": "New2024a", "match_tag": "New2024",
                "title": "A Paper", "same_file": True, "matched_on": "file and title",
                "level": "danger", "match_has_doc": True, "resolvable": False,
                "message": "New2024a has the same file and title as New2024.",
            }
            return {"bibtex": "@article{New2024a}", "tag": "New2024a",
                    "analysis": pd.DataFrame(), "blocked": True,
                    "blocked_message": conflict["message"], "conflict": conflict}

    html = _stage_one(_batch_app(monkeypatch, FakeLibrary()).test_client()).get_data(as_text=True)

    assert "alert-danger" in html and "Same file and title" in html
    assert "btn-danger" in html and "Import anyway as New2024a" in html
    assert "Replace document on" not in html


def test_ingest_replace_calls_library_and_advances(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def replace_document(self, tag, path, **kwargs):
            self.calls["replace"] = (tag, path, kwargs)
            return {"tag": tag, "hash": "newhash", "version": 0,
                    "path": "x.pdf", "demoted": ["oldhash"]}

    lib = FakeLibrary()
    app = _batch_app(monkeypatch, lib)
    client = app.test_client()

    start = _stage_one(client).get_data(as_text=True)
    batch_id = start.split('name="batch_id" value="')[1].split('"')[0]

    response = client.post(
        "/ingest/replace",
        data={"bibtex": "@article{x}", "batch_id": batch_id, "idx": "0",
              "hash": "newhash", "match_tag": "Old2020", "update_metadata": "on"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "is now the primary document for" in html
    assert "Old2020" in html
    tag, _path, kwargs = lib.calls["replace"]
    assert tag == "Old2020"
    assert kwargs["known_hash"] == "newhash"
    assert kwargs["update_bibtex"] == "@article{x}"


# ------------------------------------------------------------- reference only

REF_BIB = "@article{Smith2024,\n  author = {Smith, John},\n  title = {No File},\n  year = {2024},\n}"


def test_reference_only_workbench_previews_without_a_document(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")
    lib = _FakePreviewLibrary()
    client = _batch_app(monkeypatch, lib).test_client()

    html = client.post("/ingest/reference", data={"bibtex": REF_BIB}).get_data(as_text=True)

    assert "Reference only" in html
    assert "GO: Add Reference" in html
    assert lib.calls["preview_path"] is None
    # nothing staged: no batch, no file pane
    assert not (tmp_path / "staging").exists()
    assert "view-temp" not in html


def test_reference_only_commit_adds_reference_with_no_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def import_staged_document(self, bibtex, staged_document_path=None, **kwargs):
            self.calls["commit_path"] = staged_document_path
            self.calls["commit_kwargs"] = kwargs
            return SimpleNamespace(ref_df=pd.DataFrame([{"tag": "Smith2024"}]))

    lib = FakeLibrary()
    client = _batch_app(monkeypatch, lib).test_client()

    html = client.post("/ingest/reference/commit", data={"bibtex": REF_BIB}).get_data(as_text=True)

    assert lib.calls["commit_path"] is None
    assert "added as a reference with no document" in html
    assert "/edit?tag=Smith2024" in html
    assert "Zone A: The Documents" in html


def test_reference_only_commit_blocked_keeps_the_workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def import_staged_document(self, *args, **kwargs):
            raise LibraryImportBlocked("Smith2024 has the same title as Other2020.")

    client = _batch_app(monkeypatch, FakeLibrary()).test_client()

    html = client.post("/ingest/reference/commit", data={"bibtex": REF_BIB}).get_data(as_text=True)

    assert "alert-warning" in html and "same title as Other2020" in html
    assert "GO: Add Reference" in html
    assert "title = {No File}" in html


def test_reference_only_import_anyway_reaches_the_library(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")

    class FakeLibrary(_FakePreviewLibrary):
        def import_staged_document(self, bibtex, staged_document_path=None, **kwargs):
            self.calls["commit_kwargs"] = kwargs
            return SimpleNamespace(ref_df=pd.DataFrame([{"tag": "Smith2024a"}]))

    lib = FakeLibrary()
    client = _batch_app(monkeypatch, lib).test_client()

    html = client.post("/ingest/reference/commit",
                       data={"bibtex": REF_BIB, "import_anyway": "on"}).get_data(as_text=True)

    assert lib.calls["commit_kwargs"]["allow_duplicate"] is True
    assert "added as a reference with no document" in html


# ------------------------------------------------------------- real importer

def test_same_file_and_title_imports_anyway_through_the_real_importer(tmp_library, tmp_path):
    """SKIP (Dupe) is held, red, then Import anyway adds a second reference to the file."""
    from archivum.hasher import hash_many3

    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged = (staged_dir / "paper.pdf").resolve()
    staged.write_bytes(b"%PDF-1.4\n% import anyway probe\n")
    h = hash_many3([staged], workers=1)[staged]
    tz = "Europe/London"
    lib = tmp_library(
        [{"tag": "Smith2024", "type": "article", "author": "Smith, John",
          "title": "A Probe Paper", "year": "2024", "journal": "J", "doi": "",
          "added": pd.Timestamp("2024-01-01", tz=tz)}],
        [{"hash": h, "version": 0, "path": "ab/existing.pdf",
          "create": pd.Timestamp("2024-01-01", tz=tz)}],
        [{"tag": "Smith2024", "hash": h, "version": 0, "priority": 0}],
    )
    bib = ("@article{Smith2024,\n  author = {Smith, John},\n  title = {A Probe Paper},\n"
           "  journal = {J},\n  year = {2024},\n}")

    preview = lib.preview_staged_document_import(bib, staged, known_hash=h)
    conflict = preview["conflict"]
    assert conflict["action"] == "SKIP (Dupe)"
    assert conflict["level"] == "danger" and conflict["match_tag"] == "Smith2024"
    # the preview keeps the entry, so it shows the tag Import anyway would take
    assert preview["tag"] != "Smith2024"

    with pytest.raises(LibraryImportBlocked, match="same file and title"):
        lib.import_staged_document(bib, staged, known_hash=h, extract_text=False)

    importer = lib.import_staged_document(bib, staged, known_hash=h, extract_text=False,
                                          allow_duplicate=True)

    new_tag = str(importer.ref_df.iloc[0].tag)
    assert new_tag != "Smith2024"
    refs = pd.read_feather(lib.config_path / "ref.feather")
    assert {"Smith2024", new_tag} <= set(refs.tag)
    links = pd.read_feather(lib.config_path / "ref-doc.feather")
    assert set(links[links.hash == h].tag) == {"Smith2024", new_tag}
