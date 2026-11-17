"""Citation counts in ``Library.database`` and so in querex; every write hits a temp library."""

from __future__ import annotations

import os

import pandas as pd
import pytest

from archivum.doi_audit import journal
from archivum.search.universe import _library_signature, resolve_universe_tags
from archivum.utilities import format_count

REFS = [
    {"tag": "Wang2020", "type": "article", "title": "One Work", "author": "Wang, Ruodu", "year": "2020", "doi": "10.1/one"},
    {"tag": "Wang2019", "type": "article", "title": "Two", "author": "Wang, Ruodu", "year": "2019", "doi": "10.1/two"},
    {"tag": "Doe2016", "type": "article", "title": "Five", "author": "Doe, Jane", "year": "2016", "doi": None},
]


def _count(lib, tag, count, doi="10.1/x"):
    journal.append_lines(lib.config_path, journal.COUNTS, [
        {"tag": tag, "source": "openalex", "source_id": "W1", "doi": doi, "count": count}])
    journal.derive_cites(lib.config_path)


def _later(lib):
    """Move ``cites.feather`` a second on, as a later run would; two writes can share a timestamp."""
    path = journal.audit_dir(lib.config_path) / journal.CITES
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 1))


@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


@pytest.fixture()
def counted(lib):
    _count(lib, "Wang2020", 120)
    _count(lib, "Wang2019", 7)
    return lib


def _tags(lib, expression):
    return list(lib.database.querex(expression)["tag"])


def test_a_query_on_cites_selects_references_with_counts(counted):
    assert _tags(counted, "!wang where cites > 100") == ["Wang2020"]
    assert _tags(counted, "where cites > 0") == ["Wang2020", "Wang2019"]
    assert _tags(counted, "select tag, cites order by -cites")[:2] == ["Wang2020", "Wang2019"]


def test_a_reference_with_no_count_is_excluded_without_error(counted):
    assert "Doe2016" not in _tags(counted, "where cites > 0")
    assert "Doe2016" not in _tags(counted, "where cites < 1000")
    assert _tags(counted, "where cites_1y > 0") == []


def test_a_library_with_no_counts_still_has_the_columns(lib):
    database = lib.database
    assert {"cites", "cites_asof", "cites_1y"} <= set(database.columns)
    assert database["cites"].isna().all() and str(database["cites"].dtype) == "Int64"
    assert _tags(lib, "where cites > 0") == []


def test_the_database_keeps_one_row_per_reference_in_order(counted):
    assert list(counted.database["tag"]) == [r["tag"] for r in REFS]
    row = counted.database.set_index("tag").loc["Wang2020"]
    assert row["cites"] == 120 and len(row["cites_asof"]) == 10


def test_counts_never_reach_the_references_or_the_bibtex(counted):
    bib = counted.config_path / "bibtex.bib"
    before_bib, before_ref = bib.read_bytes(), pd.read_feather(counted.config_path / "ref.feather")
    counted.database  # the join
    assert "cites" not in counted.ref_df.columns

    counted.save()
    after_ref = pd.read_feather(counted.config_path / "ref.feather")
    assert bib.read_bytes() == before_bib
    assert list(after_ref.columns) == list(before_ref.columns) and "cites" not in after_ref.columns


def test_new_counts_drop_the_cached_database_once(counted):
    first = counted.database
    assert counted.refresh_cites() is False and counted.database is first

    _count(counted, "Doe2016", 3)
    _later(counted)
    assert counted.refresh_cites() is True
    assert _tags(counted, "where cites > 0") == ["Wang2020", "Wang2019", "Doe2016"]
    assert counted.refresh_cites() is False


def test_cached_universes_are_dropped_when_counts_change(counted):
    signature = _library_signature(counted)
    assert resolve_universe_tags(counted, "where cites > 0") == ["Wang2020", "Wang2019"]
    _count(counted, "Doe2016", 3)
    _later(counted)
    assert _library_signature(counted) != signature


def test_the_query_page_sees_new_counts_on_the_next_request(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()

    assert "Wang2020" not in client.get("/search?q=q where cites > 100").get_data(as_text=True)
    _count(lib, "Wang2020", 120)
    _later(lib)
    html = client.get("/search?q=q where cites > 100").get_data(as_text=True)
    assert "Wang2020" in html and "Wang2019" not in html


def test_the_query_result_line_shows_the_count_between_author_and_hash(counted, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: counted))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    _count(counted, "Wang2020", 12345)
    _later(counted)
    application = create_app()
    application.config.update(TESTING=True)
    html = application.test_client().get("/search?q=q tag ~ /20/").get_data(as_text=True)

    lines = {tag: html[html.index(f">{tag}<"):].split('class="list-item', 1)[0] for tag in ("Wang2020", "Wang2019", "Doe2016")}
    assert "12,345 cites" in lines["Wang2020"] and "7 cites" in lines["Wang2019"]
    assert "cites" not in lines["Doe2016"]
    assert lines["Wang2020"].index('class="author') < lines["Wang2020"].index("12,345 cites")
    assert 'title="Citations at OpenAlex, as of 20' in lines["Wang2020"]


def test_counts_format_with_commas_and_a_dash_for_none():
    assert [format_count(x) for x in (0, 7, 12345, pd.NA, float("nan"))] == ["0", "7", "12,345", "-", "-"]
