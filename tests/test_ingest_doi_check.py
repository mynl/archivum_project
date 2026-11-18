"""The DOI check at Ingest: the entry's DOI is checked before the import, and its count stored after."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from archivum import egress
from archivum.doi_audit import doi as rules
from archivum.doi_audit import ingest_check, journal
from archivum.web.services import ingest_batch as ib

TZ = "Europe/London"
REFS = [
    {"tag": "Old2010", "type": "article", "title": "An Older Paper", "author": "Old, Olive", "year": "2010",
     "journal": "J", "doi": "", "added": pd.Timestamp("2024-01-01", tz=TZ)},
]
# what the fake OpenAlex has, by DOI in compared form: title, first author, year, count
OPENALEX = {
    "10.1/good": ("Coherent measures of risk", "Philippe Artzner", 1999, 900),
    "10.1/junk": ("Summary for Policymakers", "Raquel Vilaca", 2014, 24747),
    "10.1/weak": ("Coherent measures of risk revisited", "Philippe Artzner", 1999, 5),
    "10.1/year": ("Coherent measures of risk", "Philippe Artzner", 2010, 7),
}
CROSSREF = {"10.1/crossref-only": ("Coherent Measures of Risk", ("Philippe", "Artzner"), 1999)}
REGISTERED_ELSEWHERE = {"10.5281/zenodo.1"}


def _bib(doi=None, title="Coherent Measures of Risk", tag="Artzner1999"):
    line = f"  doi = {{{doi}}},\n" if doi is not None else ""
    return (f"@article{{{tag},\n  author = {{Artzner, Philippe and Delbaen, Freddy}},\n  title = {{{title}}},\n"
            f"  journal = {{Mathematical Finance}},\n  year = {{1999}},\n{line}}}")


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code, self._payload, self.headers = status_code, payload or {}, {}

    def json(self):
        return self._payload


class Sources:
    """A transport answering for the three sources, recording every call."""

    def __init__(self, down=False):
        self.calls, self.down = [], down

    def __call__(self, method, url, *, params, headers, timeout):
        self.calls.append((method, url, json.dumps(params or {})))
        if self.down:
            raise requests.ConnectionError("no route")
        if url.startswith("https://api.openalex.org/works/doi:"):
            doi = url.rsplit("/works/doi:", 1)[1]
            if doi not in OPENALEX:
                return FakeResponse(404)
            title, author, year, count = OPENALEX[doi]
            return FakeResponse(payload={
                "id": "https://openalex.org/W42", "doi": f"https://doi.org/{doi}", "title": title,
                "publication_year": year, "cited_by_count": count, "type": "article",
                "authorships": [{"author": {"display_name": author}}],
                "primary_location": {"source": {"display_name": "Mathematical Finance"}}, "biblio": {}})
        if url.startswith("https://api.crossref.org/works/"):
            doi = url.removeprefix("https://api.crossref.org/works/")
            if doi not in CROSSREF:
                return FakeResponse(404)
            title, (given, family), year = CROSSREF[doi]
            return FakeResponse(payload={"message": {
                "DOI": doi, "title": [title], "author": [{"given": given, "family": family}],
                "issued": {"date-parts": [[year, 3]]}, "container-title": ["J."], "type": "journal-article"}})
        doi = url.removeprefix("https://doi.org/")
        return FakeResponse(302 if doi in REGISTERED_ELSEWHERE else 404)


def _egress(transport):
    return egress.Egress(egress.SPEEDY, contact_email="", transport=transport, sleep=lambda seconds: None)


# ---- the check ----

@pytest.mark.parametrize("doi, status, source, count", [
    ("10.1/good", rules.VERIFIED, "openalex", 900),
    ("https://doi.org/10.1/GOOD", rules.VERIFIED, "openalex", 900),
    ("10.1/junk", rules.MISMATCH, "openalex", 24747),
    ("10.1/weak", rules.WEAK_MATCH, "openalex", 5),
    ("10.1/year", rules.YEAR_DIFFERS, "openalex", 7),
    ("10.1/crossref-only", rules.VERIFIED, "crossref", None),
    ("10.5281/zenodo.1", rules.NOT_COMPARED, "doi.org", None),
    ("10.1/nowhere", rules.UNRESOLVED, "doi.org", None),
])
def test_statuses_from_the_sources(doi, status, source, count):
    check = ingest_check.check_entry_doi(_bib(doi), _egress(Sources()))
    assert (check.status, check.source, check.count) == (status, source, count)
    assert check.needs_confirmation is (status != rules.VERIFIED)


def test_an_entry_with_no_doi_or_no_real_doi_sends_nothing():
    transport = Sources()
    none = ingest_check.check_entry_doi(_bib(), _egress(transport))
    bad = ingest_check.check_entry_doi(_bib("see publisher"), _egress(transport))
    assert (none.status, none.needs_confirmation) == (ingest_check.NO_DOI, False)
    assert (bad.status, bad.needs_confirmation) == (ingest_check.NOT_A_DOI, True)
    assert transport.calls == [] and ingest_check.result_sentence(none) == ""


def test_a_source_that_cannot_be_reached_is_tried_once_and_does_not_hold_the_import():
    transport = Sources(down=True)
    check = ingest_check.check_entry_doi(_bib("10.1/good"), _egress(transport))
    assert check.status == ingest_check.NOT_CHECKED and not check.needs_confirmation
    assert len(transport.calls) == 1 and "DOI not checked" in ingest_check.result_sentence(check)


def test_only_the_doi_is_sent():
    transport = Sources()
    ingest_check.check_entry_doi(_bib("10.1/nowhere"), _egress(transport))
    sent = json.dumps(transport.calls)
    assert len(transport.calls) == 3
    assert "Artzner" not in sent and "Coherent" not in sent and "1999" not in sent


# ---- the routes, through the real importer on a temporary library ----

@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


@pytest.fixture()
def client(lib, monkeypatch, tmp_path):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")
    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    transport = Sources()
    client_for_lookups = _egress(transport)
    monkeypatch.setattr(egress, "default", lambda: client_for_lookups)
    application = create_app()
    application.config.update(TESTING=True)
    test_client = application.test_client()
    test_client.transport = transport
    return test_client


def _ref_bytes(lib):
    return (lib.config_path / "ref.feather").read_bytes()


def _tags(lib):
    return set(pd.read_feather(lib.config_path / "ref.feather")["tag"])


def _add(client, bibtex, **form):
    return client.post("/ingest/reference/commit", data={"bibtex": bibtex, **form}).get_data(as_text=True)


def test_a_matching_doi_is_added_with_its_count_and_no_extra_step(client, lib):
    html = _add(client, _bib("10.1/good"))

    assert "added as a reference with no document" in html
    assert "DOI verified at OpenAlex. Cited 900 times." in html and "ingest-doi-warning" not in html
    new = (_tags(lib) - {"Old2010"}).pop()
    counts = [json.loads(line) for line in (lib.config_path / "doi-audit" / "counts.jsonl").read_text().splitlines()]
    assert [(c["tag"], c["doi"], c["count"], c["source_id"]) for c in counts] == [(new, "10.1/good", 900, "W42")]
    lib.reset()
    assert list(lib.database.querex("where cites > 0")["tag"]) == [new]
    # the Audit page has the reference as checked
    latest = journal.latest_observations(lib.config_path)
    assert latest[(new, "openalex", "doi", "10.1/good")]["status"] == journal.OK


def test_a_mismatched_doi_writes_nothing_until_it_is_confirmed(client, lib):
    before = _ref_bytes(lib)
    html = _add(client, _bib("10.1/junk"))

    assert _ref_bytes(lib) == before and not (lib.config_path / "doi-audit" / "counts.jsonl").exists()
    assert "Not added. This DOI is recorded for another work" in html
    assert 'id="ingest-doi-warning"' in html and "Summary for Policymakers" in html and "Raquel Vilaca" in html
    assert 'name="doi_confirmed" value="10.1/junk"' in html and "The DOI is right, import" in html
    assert "GO: Add Reference" in html and "Coherent Measures of Risk" in html

    html = _add(client, _bib("10.1/junk"), doi_confirmed="10.1/junk")
    assert "added as a reference with no document" in html
    assert "DOI kept at your word (mismatch). Cited 24,747 times, for that DOI." in html
    assert len(_tags(lib)) == 2


def test_confirming_one_doi_does_not_confirm_another(client, lib):
    before = _ref_bytes(lib)
    html = _add(client, _bib("10.1/weak"), doi_confirmed="10.1/junk")
    assert _ref_bytes(lib) == before and 'name="doi_confirmed" value="10.1/weak"' in html
    assert "The title is only close" in html


@pytest.mark.parametrize("doi", ["10.1/weak", "10.1/year", "10.1/nowhere", "10.5281/zenodo.1", "see publisher"])
def test_anything_short_of_a_full_match_is_held(client, lib, doi):
    before = _ref_bytes(lib)
    assert 'id="ingest-doi-warning"' in _add(client, _bib(doi)) and _ref_bytes(lib) == before


def test_an_entry_with_no_doi_is_added_and_sends_nothing(client, lib):
    html = _add(client, _bib())
    assert "added as a reference with no document" in html and "DOI" not in html.split("added as a reference")[1][:200]
    assert client.transport.calls == [] and len(_tags(lib)) == 2


def test_unreachable_sources_do_not_stop_the_import(client, lib):
    client.transport.down = True
    html = _add(client, _bib("10.1/good"))
    assert "added as a reference with no document" in html and "DOI not checked" in html
    assert len(_tags(lib)) == 2


def test_a_duplicate_is_reported_before_anything_is_sent(client, lib):
    html = _add(client, _bib("10.1/junk", title="An Older Paper", tag="Old2010"))
    assert "Not added." in html and "ingest-doi-warning" not in html
    assert client.transport.calls == [] and _tags(lib) == {"Old2010"}


def test_refreshing_the_preview_sends_nothing(client):
    html = client.post("/ingest/reference/preview", data={"bibtex": _bib("10.1/junk")}).get_data(as_text=True)
    assert "ingest-doi-warning" not in html and client.transport.calls == []


# ---- the document route, with the library's import stubbed ----

class _FakeLibrary:
    is_empty = False
    needs_reload = False
    ref_df = pd.DataFrame({"tag": []})
    doc_df = pd.DataFrame({"hash": [], "version": []})
    ref_doc_df = pd.DataFrame({"tag": [], "hash": [], "version": []})

    def __init__(self, config_path):
        self.config_path, self.imports = config_path, 0

    def refresh_cites(self):
        return False

    def preview_staged_document_import(self, bibtex, staged_document_path, **kwargs):
        return {"bibtex": bibtex, "tag": "Artzner1999", "analysis": pd.DataFrame(), "blocked": False,
                "blocked_message": "", "conflict": None}

    def import_staged_document(self, bibtex, staged_document_path=None, **kwargs):
        self.imports += 1
        return SimpleNamespace(ref_df=pd.DataFrame([{"tag": "Artzner1999"}]))


def test_the_document_route_holds_a_mismatch_and_reports_a_match(tmp_path, monkeypatch):
    from io import BytesIO

    from archivum.cli import LibraryContext
    from archivum.web.app import create_app
    from archivum.web.routes import ingest as ingest_routes

    monkeypatch.setattr(ib, "STAGING_ROOT", tmp_path / "staging")
    fake = _FakeLibrary(tmp_path / "library")
    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: fake))
    monkeypatch.setattr(ingest_routes, "_prepare", lambda path: {
        "hash": "abc123", "bibtex": _bib("10.1/good"), "candidates": {}, "error": None})
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    lookups = _egress(Sources())
    monkeypatch.setattr(egress, "default", lambda: lookups)
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    client.post("/ingest/start", data={"file": (BytesIO(b"%PDF-1.4\n"), "paper.pdf")}, content_type="multipart/form-data")
    batch_id = next((tmp_path / "staging").iterdir()).name
    form = {"batch_id": batch_id, "idx": "0", "hash": "abc123"}

    # the entry committed is the pasted one, whose DOI is another work's, not the suggestion's
    held = client.post("/ingest/commit", data={**form, "bibtex": _bib("10.1/junk")}).get_data(as_text=True)
    assert fake.imports == 0 and "paper.pdf: not imported." in held and 'name="doi_confirmed" value="10.1/junk"' in held
    assert f'name="batch_id" value="{batch_id}"' in held

    done = client.post("/ingest/commit", data={**form, "bibtex": _bib("10.1/good")}).get_data(as_text=True)
    assert fake.imports == 1 and "archived as" in done and "DOI verified at OpenAlex. Cited 900 times." in done
    assert (fake.config_path / "doi-audit" / "cites.feather").exists()
