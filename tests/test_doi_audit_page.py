"""DOI normalization, the tag-level universe, and the Audit page; every write hits a temp library."""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from archivum.doi_audit import changesets as cs
from archivum.doi_audit import journal
from archivum.doi_audit.doi import ATTENTION, MALFORMED, classify_stored_doi, doi_key, normalize_doi
from archivum.doi_audit.proposals import local_proposals, summarize
from archivum.search.universe import resolve_universe_tags

TZ = "Europe/London"
H1 = "a1" + "0" * 62

REFS = [
    {"tag": "Wang2020", "type": "article", "title": "One Work", "author": "Wang, Ruodu", "year": "2020",
     "journal": "J. One", "doi": "https://doi.org/10.1/One"},
    {"tag": "Wang2019", "type": "article", "title": "Two", "author": "Wang, Ruodu", "year": "2019",
     "journal": "J. Two", "doi": "doi:10.1/two."},
    {"tag": "Roe2018", "type": "book", "title": "Three", "author": "Roe, R.", "year": "2018",
     "journal": "", "doi": "10.1/ONE"},
    {"tag": "Poe2017", "type": "misc", "title": "Four", "author": "Poe, P.", "year": "2017",
     "journal": "", "doi": "see publisher"},
    {"tag": "Doe2016", "type": "article", "title": "Five", "author": "Doe, Jane", "year": "2016",
     "journal": "J. Five", "doi": None},
    {"tag": "Doe2015", "type": "article", "title": "Six", "author": "Doe, Jane", "year": "2015",
     "journal": "J. Six", "doi": "10.1/six"},
]
# only Wang2020 has a document
DOCS = [{"hash": H1, "version": 0, "path": "a1/one.pdf", "name": "one.pdf", "suffix": ".pdf",
         "size": 1000, "create": pd.Timestamp("2020-01-01", tz=TZ)}]
LINKS = [{"tag": "Wang2020", "hash": H1, "version": 0, "priority": 0}]


@pytest.mark.parametrize("stored, normal", [
    ("https://doi.org/10.1/x", "10.1/x"),
    ("http://doi.org/10.1/x", "10.1/x"),
    ("http://dx.doi.org/10.1/x", "10.1/x"),
    ("HTTPS://WWW.DOI.ORG/10.1/x", "10.1/x"),
    ("doi:10.1/x", "10.1/x"),
    ("DOI: 10.1/x", "10.1/x"),
    ("  10.1/x  ", "10.1/x"),
    ("10.1/x.", "10.1/x"),
    ("10.1/x,", "10.1/x"),
    ("10.1/x;", "10.1/x"),
    ("10.1%2Fx", "10.1/x"),
    ("doi: https://doi.org/10.1%2Fx. ", "10.1/x"),
    ("10.1016/S0167-6687(02)00123-X", "10.1016/S0167-6687(02)00123-X"),
])
def test_normalization(stored, normal):
    assert normalize_doi(stored) == normal


def test_normalization_keeps_case_and_the_key_folds_it():
    assert normalize_doi("https://doi.org/10.1016/S0167-6687") == "10.1016/S0167-6687"
    assert doi_key("10.1016/S0167-6687") == doi_key("https://doi.org/10.1016/s0167-6687")


@pytest.mark.parametrize("stored, cls", [
    ("10.1/x", None),
    ("https://doi.org/10.1/x", MALFORMED),
    ("10.1/x.", MALFORMED),
    ("see publisher", ATTENTION),
    ("https://example.org/paper", ATTENTION),
    ("10.1", ATTENTION),
    ("10.1/x y", ATTENTION),
])
def test_classes_from_the_text_alone(stored, cls):
    assert classify_stored_doi(stored) == cls


def test_a_value_that_is_not_a_doi_gets_no_proposal():
    rows = pd.DataFrame(REFS)
    assert [p.tag for p in local_proposals(rows, MALFORMED)] == ["Wang2020", "Wang2019"]
    attention = local_proposals(rows, ATTENTION)
    assert [(p.tag, p.proposed) for p in attention] == [("Poe2017", None)]


def test_summary_counts():
    assert summarize(pd.DataFrame(REFS)) == {
        "references": 6, "with_doi": 5, "without_doi": 1, "malformed": 2, "attention": 1, "shared": 2,
    }


@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS, DOCS, LINKS)
    library.save()
    return library


def test_the_tag_universe_keeps_a_reference_with_no_document(lib):
    assert resolve_universe_tags(lib, "") == [r["tag"] for r in REFS]
    assert resolve_universe_tags(lib, "author ~ /Wang/") == ["Wang2020", "Wang2019"]
    assert resolve_universe_tags(lib, "q author ~ /Doe/") == ["Doe2016", "Doe2015"]


def test_a_ripgrep_part_keeps_only_tags_whose_primary_document_matched(lib, monkeypatch):
    monkeypatch.setattr("archivum.search.universe._ripgrep_hash_prefixes",
                        lambda lib, part, case_sensitive=False: ({H1[:10].upper()}, False))
    assert resolve_universe_tags(lib, "author ~ /Wang/ rg risk") == ["Wang2020"]
    assert resolve_universe_tags(lib, "rg risk") == ["Wang2020"]


def test_a_query_that_does_not_parse_raises(lib):
    with pytest.raises(ValueError):
        resolve_universe_tags(lib, "year == 2020")


@pytest.fixture()
def client(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    application = create_app()
    application.config.update(TESTING=True)
    return application.test_client()


def _disk(lib):
    return pd.read_feather(lib.config_path / "ref.feather").set_index("tag")


def _form(*rows, cls="malformed", q=""):
    """A review form with the given ``(tag, current, proposed, new)`` rows ticked."""
    data = {"q": q, "cls": cls, "page": "1", "row": []}
    for tag, current, proposed, new in rows:
        key = f"{tag}|doi"
        data["row"].append(key)
        data[f"old::{key}"] = json.dumps(current)
        data[f"proposed::{key}"] = proposed
        data[f"new::{key}"] = new
    return data


@pytest.mark.parametrize("method, path", [
    ("get", "/audit"), ("get", "/audit/subset"), ("get", "/audit/review?cls=malformed"),
    ("post", "/audit/commit"), ("post", "/audit/reject"), ("post", "/audit/revert"),
])
def test_a_visitor_from_the_bridge_is_refused(client, method, path):
    response = getattr(client, method)(path, environ_overrides={"REMOTE_ADDR": "10.8.0.1"})
    assert response.status_code == 403


def test_the_menu_shows_audit_to_an_admin_only(client):
    assert 'href="/audit"' in client.get("/audit").get_data(as_text=True)
    visitor = client.get("/help", environ_overrides={"REMOTE_ADDR": "10.8.0.1"}).get_data(as_text=True)
    assert 'href="/audit"' not in visitor and "screen-audit" not in visitor
    assert "screen-audit" in client.get("/help").get_data(as_text=True)


def test_the_subset_shows_a_summary_and_no_review_table(client):
    html = client.get("/audit/subset?q=author ~ /Wang/").get_data(as_text=True)
    assert 'id="audit-summary"' in html and "audit-review-form" not in html
    assert "No changesets yet" in html


def test_a_bad_query_is_reported(client):
    html = client.get("/audit/subset?q=year == 2020").get_data(as_text=True)
    assert "Query not understood" in html


def test_the_review_table_lists_malformed_dois_with_nothing_ticked(client):
    html = client.get("/audit/review?cls=malformed").get_data(as_text=True)
    assert "Malformed: 2 rows" in html
    assert 'name="new::Wang2020|doi" value="10.1/One"' in html
    assert 'name="new::Wang2019|doi" value="10.1/two"' in html
    assert not re.search(r'type="checkbox"[^>]*\bchecked', html) and "Poe2017" not in html
    assert 'href="/edit?tag=Wang2020"' in html


def test_the_review_table_follows_the_subset(client):
    html = client.get("/audit/review?cls=malformed&q=author ~ /Doe/").get_data(as_text=True)
    assert "Nothing to review in this subset." in html


def test_attention_rows_have_no_tick_box(client):
    html = client.get("/audit/review?cls=attention").get_data(as_text=True)
    assert "Poe2017" in html and 'name="row"' not in html and "Commit ticked" not in html


def test_commit_writes_only_the_ticked_rows(client, lib):
    response = client.post("/audit/commit", data=_form(("Wang2020", "https://doi.org/10.1/One", "10.1/One", "10.1/One")))
    html, disk = response.get_data(as_text=True), _disk(lib)

    assert "Committed 1 of 1 rows" in html and "Malformed: 1 row<" in html
    assert disk.loc["Wang2020", "doi"] == "10.1/One" and disk.loc["Wang2019", "doi"] == "doi:10.1/two."
    [changeset] = cs.list_changesets(lib.config_path)
    assert changeset.stem in html and changeset.stem.endswith("_audit-malformed")
    [decision] = journal.read_lines(lib.config_path, journal.DECISIONS)
    assert (decision["decision"], decision["tag"], decision["outcome"]) == ("accepted", "Wang2020", "applied")


def test_commit_uses_the_edited_value(client, lib):
    client.post("/audit/commit", data=_form(("Wang2020", "https://doi.org/10.1/One", "10.1/One", " 10.1/one-fixed ")))
    assert _disk(lib).loc["Wang2020", "doi"] == "10.1/one-fixed"


def test_commit_with_nothing_ticked_changes_nothing(client, lib):
    html = client.post("/audit/commit", data={"q": "", "cls": "malformed", "page": "1"}).get_data(as_text=True)
    assert "No rows ticked." in html and cs.list_changesets(lib.config_path) == []


def test_a_row_changed_since_review_is_skipped_and_said_so(client, lib):
    html = client.post("/audit/commit", data=_form(("Wang2020", "https://doi.org/10.1/stale", "10.1/One", "10.1/One"))).get_data(as_text=True)
    assert "Committed 0 of 1 rows" in html and "1 skipped" in html
    assert _disk(lib).loc["Wang2020", "doi"] == "https://doi.org/10.1/One"


def test_a_refused_commit_says_why(client, lib):
    data = _form(("Wang2020", "https://doi.org/10.1/One", "10.1/One", "10.1/One"))
    data["row"] = ["Wang2020|year"]
    data["old::Wang2020|year"], data["new::Wang2020|year"] = json.dumps("2020"), "2021"
    html = client.post("/audit/commit", data=data).get_data(as_text=True)

    assert "Nothing committed." in html and "not one the commit path writes" in html
    assert _disk(lib).loc["Wang2020", "year"] == "2020"


def test_a_rejected_proposal_does_not_reappear(client, lib):
    html = client.post("/audit/reject", data=_form(("Wang2019", "doi:10.1/two.", "10.1/two", "10.1/two"))).get_data(as_text=True)

    assert "Rejected 1 rows" in html and "Malformed: 1 row<" in html
    assert "Wang2019|doi" not in client.get("/audit/review?cls=malformed").get_data(as_text=True)
    assert _disk(lib).loc["Wang2019", "doi"] == "doi:10.1/two."


def test_revert_from_the_page(client, lib):
    client.post("/audit/commit", data=_form(("Wang2020", "https://doi.org/10.1/One", "10.1/One", "10.1/One")))
    [changeset] = cs.list_changesets(lib.config_path)
    html = client.post("/audit/revert", data={"changeset": changeset.stem, "q": "", "cls": "", "page": "1"}).get_data(as_text=True)

    assert "Reverted 1 of 1 cells" in html and "applied, reverted" in html
    assert _disk(lib).loc["Wang2020", "doi"] == "https://doi.org/10.1/One"
    assert html.count("Revert</button>") == 1  # the revert itself can be reverted; the original cannot again


def test_revert_of_an_unknown_changeset_is_reported(client):
    html = client.post("/audit/revert", data={"changeset": "nothing"}).get_data(as_text=True)
    assert "Nothing reverted." in html


def test_the_review_table_is_paged(client, lib, monkeypatch):
    monkeypatch.setattr("archivum.web.routes.audit.PAGE_ROWS", 1)
    first = client.get("/audit/review?cls=malformed").get_data(as_text=True)
    second = client.get("/audit/review?cls=malformed&page=2").get_data(as_text=True)

    assert "Page 1 of 2" in first and "Wang2020|doi" in first and "Wang2019|doi" not in first
    assert "Page 2 of 2" in second and "Wang2019|doi" in second


def test_a_changeset_can_be_viewed(client, lib):
    client.post("/audit/commit", data=_form(
        ("Wang2020", "https://doi.org/10.1/One", "10.1/One", "10.1/One"),
        ("Wang2019", "doi:10.1/stale", "10.1/two", "10.1/two")))
    [changeset] = cs.list_changesets(lib.config_path)
    html = client.get(f"/audit/changeset?changeset={changeset.stem}").get_data(as_text=True)
    view = html[html.index('id="audit-changeset-view"'):html.index('id="audit-changesets"')]

    assert changeset.stem in view and "Outcome: applied." in view
    assert "https://doi.org/10.1/One" in view and ">10.1/One<" in view
    assert "skipped" in view and "changed since review" in view
    assert "No changeset" in client.get("/audit/changeset?changeset=nothing").get_data(as_text=True)
    assert client.get("/audit/changeset", environ_overrides={"REMOTE_ADDR": "10.8.0.1"}).status_code == 403
