from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from archivum.library import Library, LibraryImportBlocked


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

    with pytest.raises(LibraryImportBlocked, match="Merge/Warn"):
        lib.import_staged_document(
            "@article{smith2024, title = {A Paper}, year = {2024}}",
            staged,
            extract_text=False,
        )


def test_ingest_commit_delegates_to_library_helper(tmp_path, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    calls = {}

    class FakeLibrary:
        is_empty = False
        needs_reload = False
        ref_df = pd.DataFrame({"tag": []})

        def import_staged_document(self, bibtex, staged_document_path, **kwargs):
            calls["bibtex"] = bibtex
            calls["path"] = staged_document_path
            calls["kwargs"] = kwargs
            return SimpleNamespace(ref_df=pd.DataFrame([{"tag": "Smith2024"}]))

        def reset(self):
            raise AssertionError("test library should not reset")

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: FakeLibrary()))

    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().post(
        "/ingest/commit",
        data={
            "bibtex": "@article{smith2024,\n  title = {A Paper},\n  year = {2024},\n}",
            "temp_path": str(staged),
            "hash": "abc123",
        },
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Successfully archived" in html
    assert "Smith2024" in html
    assert calls["path"] == staged
    assert calls["kwargs"]["known_hash"] == "abc123"
    assert calls["kwargs"]["source_label"] == "web-ingest"


def test_ingest_commit_renders_duplicate_block_alert(tmp_path, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")

    class FakeLibrary:
        is_empty = False
        needs_reload = False
        ref_df = pd.DataFrame({"tag": []})

        def import_staged_document(self, *args, **kwargs):
            raise LibraryImportBlocked("Import blocked: SKIP (Dupe); tag smith2024")

        def reset(self):
            raise AssertionError("test library should not reset")

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: FakeLibrary()))

    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().post(
        "/ingest/commit",
        data={
            "bibtex": "@article{smith2024,\n  title = {A Paper},\n  year = {2024},\n}",
            "temp_path": str(staged),
            "hash": "abc123",
        },
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "alert-danger" in html
    assert "SKIP (Dupe)" in html


def test_ingest_preview_route_uses_library_preview(tmp_path, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    staged = tmp_path / "paper.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    calls = {}

    class FakeLibrary:
        is_empty = False
        needs_reload = False

        def preview_staged_document_import(self, bibtex, staged_document_path, **kwargs):
            calls["bibtex"] = bibtex
            calls["path"] = staged_document_path
            calls["kwargs"] = kwargs
            return {
                "bibtex": "@article{Smith2024,\n  author = {Smith, John},\n}",
                "tag": "Smith2024",
                "analysis": pd.DataFrame([{"tag": "Smith2024", "action": "Import"}]),
                "blocked": False,
                "blocked_message": "",
            }

        def reset(self):
            raise AssertionError("test library should not reset")

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: FakeLibrary()))

    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().post(
        "/ingest/preview",
        data={
            "bibtex": "@article{rawkey,\n  title = {A Paper},\n}",
            "temp_path": str(staged),
            "hash": "abc123",
        },
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Final Tag: Smith2024" in html
    assert "Smith, John" in html
    assert calls["path"] == staged
    assert calls["kwargs"]["known_hash"] == "abc123"
