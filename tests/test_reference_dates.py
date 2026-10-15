"""
The reference date added and the document count ([added]).

Every test runs on a temporary library: these paths call ``save()``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from archivum.bibtex import dict_to_bibtex

TZ = "Europe/London"


def ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=TZ)


REFS = [
    {"tag": "Alpha2020", "type": "article", "title": "Alpha"},
    {"tag": "Beta2021", "type": "article", "title": "Beta"},
    {"tag": "Gamma2022", "type": "book", "title": "Gamma"},
]
DOCS = [
    {"hash": "h1", "version": 0, "path": "a/1.pdf", "create": ts("2024-03-01 10:00")},
    {"hash": "h2", "version": 0, "path": "a/2.pdf", "create": ts("2023-01-15 09:00")},
    {"hash": "h3", "version": 0, "path": "a/3.pdf", "create": ts("2025-06-30 12:00")},
]
# Alpha: primary h1 (2024), alternate h2 (2023, earlier). Beta: h3. Gamma: no document.
LINKS = [
    {"tag": "Alpha2020", "hash": "h1", "version": 0, "priority": 0},
    {"tag": "Alpha2020", "hash": "h2", "version": 0, "priority": 1},
    {"tag": "Beta2021", "hash": "h3", "version": 0, "priority": 0},
]


def _saved_refs(lib) -> pd.DataFrame:
    return pd.read_feather(lib.config_path / "ref.feather").set_index("tag")


def test_backfill_uses_earliest_document_then_undated(tmp_library):
    lib = tmp_library(REFS, DOCS, LINKS)

    report = lib.backfill_added("2026-01-01")
    assert report.loc["documents", "count"] == 2
    assert report.loc["undated", "count"] == 1
    # dry run writes nothing
    assert "added" not in _saved_refs(lib).columns

    lib.backfill_added("2026-01-01", execute=True)
    added = _saved_refs(lib)["added"]
    assert added["Alpha2020"] == ts("2023-01-15 09:00")  # the alternate, every priority
    assert added["Beta2021"] == ts("2025-06-30 12:00")
    assert added["Gamma2022"] == ts("2026-01-01 00:00")  # midnight local


def test_backfill_never_overwrites(tmp_library):
    refs = [dict(REFS[0], added=ts("2010-05-05")), REFS[1], REFS[2]]
    lib = tmp_library(refs, DOCS, LINKS)

    report = lib.backfill_added("2026-01-01", execute=True)

    assert report.loc["existing", "count"] == 1
    assert _saved_refs(lib).loc["Alpha2020", "added"] == ts("2010-05-05")


def test_save_stamps_missing_dates_only_once_the_column_exists(tmp_library, tmp_path):
    lib = tmp_library(REFS, DOCS, LINKS)
    lib.update_reference("", {"tag": "Delta2023", "type": "misc", "title": "Delta"})
    # not backfilled: nothing is stamped
    assert "added" not in _saved_refs(lib).columns

    lib.backfill_added("2026-01-01", execute=True)
    before = pd.Timestamp.now(tz=TZ)
    lib.update_reference("", {"tag": "Epsilon2024", "type": "misc", "title": "Epsilon"})
    saved = _saved_refs(lib)["added"]
    assert str(saved.dtype) == f"datetime64[ns, {TZ}]"
    assert saved["Epsilon2024"] >= before
    assert saved["Alpha2020"] == ts("2023-01-15 09:00")


def test_edit_and_rename_keep_the_date(tmp_library):
    lib = tmp_library(REFS, DOCS, LINKS)
    lib.backfill_added("2026-01-01", execute=True)

    # an added typed into the textarea is ignored
    lib.update_reference("Beta2021", {"tag": "Beta2021b", "type": "article", "title": "Beta!",
                                      "added": "1999-01-01"})

    saved = _saved_refs(lib)
    assert saved.loc["Beta2021b", "added"] == ts("2025-06-30 12:00")
    assert saved.loc["Beta2021b", "title"] == "Beta!"


def test_reimport_keeps_the_earlier_date(tmp_library):
    lib = tmp_library(REFS, DOCS, LINKS)
    lib.backfill_added("2026-01-01", execute=True)
    importer = SimpleNamespace(
        ref_df=pd.DataFrame([{"tag": "Beta2021", "type": "article", "title": "Beta, re-imported"},
                             {"tag": "Zeta2025", "type": "article", "title": "Zeta"}]),
        doc_df=pd.DataFrame(columns=["hash", "version", "path", "create"]),
        ref_doc_df=pd.DataFrame(columns=["tag", "hash", "version", "priority"]),
    )

    lib.update(importer)

    saved = _saved_refs(lib)
    assert saved.loc["Beta2021", "title"] == "Beta, re-imported"
    assert saved.loc["Beta2021", "added"] == ts("2025-06-30 12:00")
    assert not pd.isna(saved.loc["Zeta2025", "added"])


def test_database_counts_documents_and_falls_back_to_create(tmp_library):
    lib = tmp_library(REFS, DOCS, LINKS)

    db = lib.database.set_index("tag")
    assert db["docs"].to_dict() == {"Alpha2020": 2, "Beta2021": 1, "Gamma2022": 0}
    # not backfilled: added stands in from the primary document's create
    assert db.loc["Alpha2020", "added"] == ts("2024-03-01 10:00")
    assert pd.isna(db.loc["Gamma2022", "added"])

    ref_only = lib.database.querex("select added, * where docs == 0 order by -added")
    assert ref_only["tag"].tolist() == ["Gamma2022"]


def test_dict_to_bibtex_omits_added():
    text = dict_to_bibtex({"tag": "Alpha2020", "type": "article", "title": "Alpha",
                           "added": ts("2023-01-15")})
    assert "added" not in text
    assert "Alpha" in text
