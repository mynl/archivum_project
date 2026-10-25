"""Adding a reference with no document ([ref-only])."""

from __future__ import annotations

import pandas as pd

BIB = """@article{Probe2024,
  author = {Smith, John},
  title = {A Paper Without a File},
  journal = {Journal of Probes},
  year = {2024},
  file = {:C\\:/should/be/dropped.pdf:pdf},
}
"""

TZ = "Europe/London"
REFS = [{"tag": "Other2020", "type": "article", "title": "Something Else", "author": "Doe, Jane",
         "year": "2020", "journal": "J", "doi": "", "added": pd.Timestamp("2021-01-01", tz=TZ)}]


def test_reference_only_import_injects_no_file_and_extracts_nothing(tmp_library, monkeypatch):
    lib = tmp_library(REFS, [], [])
    calls = {}

    class FakeImporter:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs
            calls["bibtex"] = kwargs["bibtex_file_path"].read_text(encoding="utf-8")
            self.ported_df = pd.DataFrame([{"tag": "Smith2024"}])
            self.ref_df = pd.DataFrame([{"tag": "Smith2024", "type": "article"}])
            self.doc_df = pd.DataFrame(columns=["path", "hash", "version"])

        def import_bibtex_file(self):
            return pd.DataFrame()

        def import_analysis(self):
            return pd.DataFrame([{"tag": "Smith2024", "action": "Import"}])

        def update_library(self, save=True):
            calls["update_library_save"] = save

    def no_extraction(*args, **kwargs):
        raise AssertionError("a reference-only import has no text to extract")

    monkeypatch.setattr("archivum.import_bibtex.Bib2df_Incremental", FakeImporter)
    monkeypatch.setattr("archivum.library.extract_text_for_paths", no_extraction)

    lib.import_staged_document(BIB)

    assert calls["kwargs"]["doc_dir"] is None
    assert "file" not in calls["bibtex"]
    assert calls["update_library_save"] is True
    # the review bib lived in a temporary directory, not in the library
    assert not calls["kwargs"]["bibtex_file_path"].exists()
    assert not (lib.config_path / "staging").exists()


def test_reference_only_runs_the_real_importer(tmp_library):
    lib = tmp_library(REFS, [], [])

    preview = lib.preview_staged_document_import(BIB)
    assert not preview["blocked"]
    assert "file" not in preview["bibtex"]

    importer = lib.import_staged_document(BIB)

    tag = str(importer.ref_df.iloc[0].tag)
    assert tag  # allocated by the importer
    assert importer.ref_doc_df.empty
    refs = pd.read_feather(lib.config_path / "ref.feather").set_index("tag")
    # the review bib goes through dict_to_bibtex, which braces titles, as for documents
    assert refs.loc[tag, "title"].strip("{}") == "A Paper Without a File"
    assert not pd.isna(refs.loc[tag, "added"])  # stamped by save()
    assert lib.database.set_index("tag").loc[tag, "docs"] == 0


def test_reference_only_title_duplicate_blocks(tmp_library):
    lib = tmp_library(REFS, [], [])
    duplicate = BIB.replace("A Paper Without a File", "Something Else")

    preview = lib.preview_staged_document_import(duplicate)

    # the match has no document either: "Import (Fill)", held for Import anyway
    assert preview["blocked"]
    assert preview["conflict"]["match_tag"] == "Other2020"
    assert not preview["conflict"]["resolvable"]


def test_reference_only_title_duplicate_imports_anyway(tmp_library):
    lib = tmp_library(REFS, [], [])
    duplicate = BIB.replace("A Paper Without a File", "Something Else")

    preview = lib.preview_staged_document_import(duplicate)
    assert preview["conflict"]["level"] == "warning"

    importer = lib.import_staged_document(duplicate, allow_duplicate=True)

    tag = str(importer.ref_df.iloc[0].tag)
    assert tag != "Other2020"
    refs = pd.read_feather(lib.config_path / "ref.feather").set_index("tag")
    assert {"Other2020", tag} <= set(refs.index)
    assert refs.loc[tag, "title"].strip("{}") == "Something Else"
