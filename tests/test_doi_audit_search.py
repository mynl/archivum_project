"""Finding missing DOIs: candidate classes, the staged search, the review, and counts for a work with no DOI."""

from __future__ import annotations

import datetime as dt
import json
import threading

import pandas as pd
import pytest

from archivum import egress
from archivum.doi_audit import doi as rules
from archivum.doi_audit import journal, runner
from archivum.doi_audit.proposals import search_counts, searched_references

REFS = [
    {"tag": "Exact1999", "type": "article", "title": "Coherent Measures of {R}isk", "author": "Artzner, Philippe and Delbaen, F.", "year": "1999", "doi": ""},
    {"tag": "Strong2005", "type": "article", "title": "Quantitative Risk Management", "author": "McNeil, Alexander", "year": "2005", "doi": None},
    {"tag": "Two2019", "type": "article", "title": "A Paper With a Preprint", "author": "Wang, Ruodu", "year": "2019", "doi": ""},
    {"tag": "Close2010", "type": "article", "title": "Insurance Risk and Ruin", "author": "Dickson, David", "year": "2010", "doi": ""},
    {"tag": "Book1971", "type": "book", "title": "An Introduction to Probability Theory", "author": "Feller, William", "year": "1971", "doi": ""},
    {"tag": "Lost1950", "type": "techreport", "title": "An Internal Memorandum on Reserves", "author": "Doe, Jane", "year": "1950", "doi": ""},
    {"tag": "Has2020", "type": "article", "title": "Has a DOI", "author": "Roe, R.", "year": "2020", "doi": "10.1/has"},
    {"tag": "Blank2000", "type": "misc", "title": "", "author": "Roe, R.", "year": "2000", "doi": ""},
]


def _cr(doi, title, family, year, kind="journal-article", **extra):
    return {"DOI": doi, "title": [title], "author": [{"given": "A.", "family": family}],
            "issued": {"date-parts": [[year]]}, "container-title": ["J."], "type": kind, **extra}


def _oa(work_id, doi, title, name, year, count):
    return {"id": f"https://openalex.org/{work_id}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title,
            "publication_year": year, "cited_by_count": count, "type": "book" if not doi else "article",
            "authorships": [{"author": {"display_name": name}}], "primary_location": None, "biblio": {}}


# keyed by a word of the title that appears in the query
CROSSREF = {
    "coherent": [_cr("10.1111/1467-9965.00068", "Coherent Measures of Risk", "Artzner", 1999),
                 _cr("10.1/other", "Coherent risk measures revisited", "Other", 2010)],
    "quantitative": [_cr("10.1515/QRM", "Quantitative risk management", "McNeil", 2015, kind="book")],
    "preprint": [_cr("10.48550/arXiv.1901.00001", "A paper with a preprint", "Wang", 2019, kind="posted-content"),
                 _cr("10.1016/j.published", "A Paper with a Preprint", "Wang", 2020)],
    "ruin": [_cr("10.1/ruin2", "Insurance risk and ruin theory", "Dickson", 2011)],
    "probability": [_cr("10.1/unrelated", "Fish of the North Sea", "Cod", 1980)],
    "memorandum": [],
}
OPENALEX = {
    "probability": [_oa("W42", None, "An introduction to probability theory", "William Feller", 1971, 30000)],
    "memorandum": [],
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status_code, payload or {}, headers or {}

    def json(self):
        return self._payload


class Sources:
    """Answers title searches at Crossref and OpenAlex, and a work lookup by id; records every call."""

    def __init__(self, openalex_budget=None):
        self.calls, self.openalex_budget = [], openalex_budget

    def hosts(self):
        return [url.split("/")[2] for url, _ in self.calls]

    def __call__(self, method, url, *, params, headers, timeout):
        self.calls.append((url, dict(params or {})))
        if url == "https://api.crossref.org/works":
            query = params["query.bibliographic"].lower()
            items = next((v for k, v in CROSSREF.items() if k in query), [])
            return FakeResponse(payload={"message": {"items": items}})
        if url == "https://api.openalex.org/works":
            query = params["filter"].removeprefix("title.search:").lower()
            results = next((v for k, v in OPENALEX.items() if k in query), [])
            headers = {}
            if self.openalex_budget is not None:
                headers = {"X-RateLimit-Limit": "10000", "X-RateLimit-Remaining": str(self.openalex_budget)}
            return FakeResponse(payload={"results": results}, headers=headers)
        if url == "https://api.openalex.org/works/W42":
            return FakeResponse(payload=OPENALEX["probability"][0])
        return FakeResponse(404)


def _client(transport, mode=egress.SPEEDY):
    return egress.Egress(mode, contact_email="", transport=transport, sleep=lambda seconds: None)


# ---- classes ----

LIB = {"title": "A Paper With a Preprint", "author": "Wang, Ruodu", "year": "2019"}


def _record(doi, title="A paper with a preprint", author="R. Wang", year=2019, kind="journal-article", source="crossref"):
    return {"source": source, "source_id": doi, "doi": doi.lower() if doi else None, "title": title, "first_author": author,
            "year": year, "type": kind}


def test_one_exact_candidate_is_proposed_exact():
    cls, record, scored = rules.classify_candidates(LIB, [_record("10.1/a")])
    assert cls == rules.PROPOSED_EXACT and record["doi"] == "10.1/a" and len(scored) == 1


def test_a_close_title_or_a_year_apart_is_strong_not_exact():
    assert rules.classify_candidates(LIB, [_record("10.1/a", title="A paper with a preprint II")])[0] == rules.PROPOSED_STRONG
    assert rules.classify_candidates(LIB, [_record("10.1/a", year=2023)])[0] == rules.PROPOSED_STRONG


def test_the_journal_doi_wins_over_the_preprint():
    arxiv = _record("10.48550/arXiv.1901.00001", kind="posted-content")
    cls, record, _ = rules.classify_candidates(LIB, [arxiv, _record("10.1016/j.x", year=2020)])
    assert cls == rules.PROPOSED_EXACT and record["doi"] == "10.1016/j.x"
    # a preprint alone is still a proposal
    assert rules.classify_candidates(LIB, [arxiv])[1]["doi"] == "10.48550/arxiv.1901.00001"


def test_the_year_separates_an_article_from_its_reprint():
    cls, record, _ = rules.classify_candidates(LIB, [_record("10.1/reprint", year=2024), _record("10.1/article")])
    assert cls == rules.PROPOSED_EXACT and record["doi"] == "10.1/article"


def test_two_published_candidates_are_ambiguous():
    cls, record, scored = rules.classify_candidates(LIB, [_record("10.1/a"), _record("10.1/b")])
    assert cls == rules.AMBIGUOUS and record is None and len(scored) == 2


def test_the_same_doi_from_two_sources_is_one_candidate():
    both = [_record("10.1/a"), _record("10.1/a", source="openalex")]
    assert rules.classify_candidates(LIB, both)[0] == rules.PROPOSED_EXACT


@pytest.mark.parametrize("candidate, cls", [
    (_record("10.1/a", title="A paper with a preprint and a great deal more besides"), rules.AMBIGUOUS),
    (_record("10.1/a", author=None), rules.AMBIGUOUS),
    (_record("10.1/a", author="Somebody Else"), rules.AMBIGUOUS),
    (_record(None, source="openalex"), rules.WORK_NO_DOI),
    (_record("10.1/a", title="Fish of the North Sea"), rules.NOT_FOUND),
])
def test_other_classes(candidate, cls):
    assert rules.classify_candidates(LIB, [candidate])[0] == cls


def test_a_title_is_sent_without_latex_or_html():
    assert rules.plain_title("Coherent Measures of {R}isk") == "Coherent Measures of Risk"
    assert rules.plain_title('{\\"U}ber <i>die</i>  Sache, Teil $2$') == "Uber die Sache, Teil 2"


def test_no_candidates_is_not_found():
    assert rules.classify_candidates(LIB, []) == (rules.NOT_FOUND, None, [])


# ---- the run ----

@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


def _plan(lib):
    return runner.plan_search(lib.config_path, lib.ref_df, 180)


def _rows(lib):
    found = searched_references(lib.ref_df, journal.latest_observations(lib.config_path),
                                journal.rejected_proposals(lib.config_path), journal.accepted_works(lib.config_path))
    return {p.tag: p for p in found}


def _search(lib, transport=None):
    transport = transport or Sources()
    state = runner.SearchRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(transport)).run()
    return state, transport


def test_the_plan_counts_only_references_with_a_title_and_no_doi(lib):
    plan = _plan(lib)
    assert (plan.subset, plan.has_doi, plan.no_title, plan.fresh, plan.tags) == (8, 1, 1, 0, 6)
    assert (plan.requests, plan.requests_most, plan.openalex_searches) == (6, 12, (0, 6))


def test_openalex_is_searched_only_where_crossref_found_nothing_close(lib):
    state, transport = _search(lib)

    assert state["status"] == runner.FINISHED and state["action"] == "search"
    assert (state["total"], state["done"], state["requests"]) == (6, 6, 8)
    assert transport.hosts().count("api.crossref.org") == 6
    openalex = sorted(params["filter"] for url, params in transport.calls if "openalex" in url)
    assert openalex == ["title.search:An Internal Memorandum on Reserves", "title.search:An Introduction to Probability Theory"]


def test_only_title_surname_and_year_are_sent(lib):
    _, transport = _search(lib)
    queries = [params["query.bibliographic"] for url, params in transport.calls if "crossref" in url]
    # the braces of the stored title are not sent
    assert "Coherent Measures of Risk artzner 1999" in queries
    sent = json.dumps(transport.calls)
    assert "Exact1999" not in sent and "Delbaen" not in sent and "Philippe" not in sent


def test_classes_after_a_search(lib):
    _search(lib)
    rows = _rows(lib)

    assert rows["Exact1999"].cls == rules.PROPOSED_EXACT and rows["Exact1999"].proposed == "10.1111/1467-9965.00068"
    # Crossref's capitals are kept in the proposal
    assert rows["Strong2005"].cls == rules.PROPOSED_STRONG and rows["Strong2005"].proposed == "10.1515/QRM"
    assert rows["Two2019"].cls == rules.PROPOSED_EXACT and rows["Two2019"].proposed == "10.1016/j.published"
    assert sorted(c["preprint"] for c in rows["Two2019"].candidates) == [False, True]
    assert rows["Close2010"].cls == rules.AMBIGUOUS and rows["Close2010"].proposed == ""
    assert rows["Book1971"].cls == rules.WORK_NO_DOI and rows["Book1971"].work_id == "W42" and rows["Book1971"].field == "work"
    assert rows["Lost1950"].cls == rules.NOT_FOUND and rows["Lost1950"].proposed is None
    assert "Has2020" not in rows and "Blank2000" not in rows
    assert search_counts(list(rows.values())) == {
        rules.PROPOSED_EXACT: 2, rules.PROPOSED_STRONG: 1, rules.AMBIGUOUS: 1, rules.WORK_NO_DOI: 1,
        rules.NOT_FOUND: 1, rules.NOT_SEARCHED: 0}


def test_a_search_is_not_repeated_within_the_period_found_or_not(lib):
    _search(lib)
    plan = _plan(lib)
    assert (plan.fresh, plan.tags, plan.requests) == (6, 0, 0)

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=181)
    latest = journal.latest_observations(lib.config_path)
    stale = [key for key, record in latest.items() if not journal.is_fresh(record, 180, now=later)]
    assert len(stale) == len(latest)


def test_a_changed_title_is_searched_again(lib):
    _search(lib)
    lib.ref_df.loc[lib.ref_df["tag"] == "Lost1950", "title"] = "A Memorandum on Loss Reserves"
    plan = _plan(lib)
    assert list(plan.references) == ["Lost1950"] and plan.stages["Lost1950"] == runner.CROSSREF_SEARCH


def test_a_low_openalex_budget_stops_the_search_and_it_resumes(lib):
    transport = Sources(openalex_budget=100)   # 1% of the day's credits left
    state = runner.SearchRun(lib.config_path, _plan(lib), egress.POLITE, client=_client(transport, egress.POLITE)).run()

    assert state["status"] == runner.STOPPED and "budget low" in state["message"]
    assert transport.hosts().count("api.openalex.org") == 1
    plan = _plan(lib)
    assert plan.tags == 1 and set(plan.stages.values()) == {runner.OPENALEX_SEARCH}
    assert plan.requests == 1 and plan.openalex_searches == (1, 1)


def test_the_references_are_not_changed_by_a_search(lib):
    before = (lib.config_path / "ref.feather").read_bytes()
    _search(lib)
    assert (lib.config_path / "ref.feather").read_bytes() == before


def test_an_accepted_work_gets_its_count_by_id(lib):
    _search(lib)
    journal.append_lines(lib.config_path, journal.DECISIONS, [
        {"decision": journal.WORK_ACCEPTED, "tag": "Book1971", "openalex_id": "W42"}])
    plan = runner.plan_counts(lib.config_path, lib.ref_df, 30)
    assert plan.works == {"W42": ["Book1971"]} and plan.requests == 2   # the batch for Has2020, and the work

    transport = Sources()
    state = runner.CountsRun(lib.config_path, plan, egress.SPEEDY, client=_client(transport)).run()
    assert state["status"] == runner.FINISHED
    assert ("https://api.openalex.org/works/W42", {"select": "id,doi,title,publication_year,cited_by_count,type,authorships,primary_location,biblio"}) in transport.calls
    cites = journal.read_cites(lib.config_path).set_index("tag")
    assert cites.loc["Book1971", "cites"] == 30000 and cites.loc["Book1971", "openalex_id"] == "W42"
    assert runner.plan_counts(lib.config_path, lib.ref_df, 30).works == {}
    assert _rows(lib)["Book1971"].work_accepted is True


def test_a_reference_with_no_doi_and_no_accepted_work_gets_no_count(lib):
    _search(lib)
    plan = runner.plan_counts(lib.config_path, lib.ref_df, 30)
    assert plan.works == {} and plan.no_doi == 7


# ---- the page ----

@pytest.fixture()
def client(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    transport = Sources()
    real = egress.Egress
    monkeypatch.setattr(egress, "Egress", lambda mode=egress.SPEEDY, **kw: real(
        mode, contact_email="", transport=transport, sleep=lambda s: None, stop=kw.get("stop")))
    application = create_app()
    application.config.update(TESTING=True)
    test_client = application.test_client()
    test_client.transport = transport
    return test_client


def _run_search(client):
    html = client.post("/audit/run", data={"q": "", "action": "search", "mode": "speedy"}).get_data(as_text=True)
    for thread in threading.enumerate():
        if thread.name.startswith("doi-audit-run-"):
            thread.join(10)
    return html


def _form(tag, current, proposed, new, cls="proposed (exact)", field="doi"):
    key = f"{tag}|{field}"
    return {"q": "", "cls": cls, "page": "1", "row": [key], f"old::{key}": json.dumps(current),
            f"proposed::{key}": proposed, f"new::{key}": new}


def _disk(lib):
    return pd.read_feather(lib.config_path / "ref.feather").set_index("tag")


def test_the_search_plan_says_what_is_sent_and_sends_nothing(client):
    html = " ".join(client.get("/audit/plan?action=search").get_data(as_text=True).split())
    assert "<strong>6</strong> would be searched for" in html and "<strong>6 to 12</strong> requests" in html
    assert "its title, its first author's surname and its year are sent" in html
    assert "0 to 60 credits" in html and client.transport.calls == []


def test_a_search_from_the_page_then_its_results(client, lib):
    assert "Search started: 6 references, 6 requests or more, speedy." in _run_search(client)
    response = client.get("/audit/run-status")
    assert json.loads(response.headers["HX-Trigger"]) == {"audit-run-ended": {"cls": "search"}}
    assert "Last DOI search finished" in response.get_data(as_text=True)

    html = client.get("/audit/review?cls=search").get_data(as_text=True)
    assert "DOI search, Exact match: 2 rows" in html
    for pill in ("Exact match 2", "Strong match 1", "Ambiguous 1", "Work without a DOI 1", "Not found 1", "Not searched 0"):
        assert pill in html
    assert 'name="new::Exact1999|doi" value="10.1111/1467-9965.00068"' in html
    assert "Use this DOI" in html and "preprint" in html and "Searched for a DOI" in html
    assert 'type="checkbox"' in html and " checked" not in html.replace("DOIs checked", "")


def test_an_accepted_proposal_commits_the_doi(client, lib):
    _run_search(client)
    html = client.post("/audit/commit", data=_form("Exact1999", "", "10.1111/1467-9965.00068", "10.1111/1467-9965.00068")).get_data(as_text=True)

    assert "Committed 1 of 1 rows" in html and "Exact match 1" in html
    assert _disk(lib).loc["Exact1999", "doi"] == "10.1111/1467-9965.00068"
    assert _disk(lib).loc["Exact1999", "author"] == REFS[0]["author"] and _disk(lib).loc["Exact1999", "year"] == "1999"


def test_a_missing_doi_cell_is_committed_too(client, lib):
    _run_search(client)
    client.post("/audit/commit", data=_form("Strong2005", None, "10.1515/QRM", "https://doi.org/10.1515/QRM", cls="proposed (strong)"))
    assert _disk(lib).loc["Strong2005", "doi"] == "10.1515/QRM"


def test_an_ambiguous_row_needs_a_doi_picked(client, lib):
    _run_search(client)
    html = client.post("/audit/commit", data=_form("Close2010", "", "", "", cls="ambiguous")).get_data(as_text=True)
    assert "Nothing committed. No DOI given for Close2010." in html and _disk(lib).loc["Close2010", "doi"] == ""

    client.post("/audit/commit", data=_form("Close2010", "", "", "10.1/ruin2", cls="ambiguous"))
    assert _disk(lib).loc["Close2010", "doi"] == "10.1/ruin2"


def test_a_rejected_proposal_does_not_reappear(client, lib):
    _run_search(client)
    html = client.post("/audit/reject", data=_form("Exact1999", "", "10.1111/1467-9965.00068", "x")).get_data(as_text=True)
    assert "Rejected 1 rows" in html and "Exact match 1" in html
    assert "Exact1999" not in client.get("/audit/review?cls=proposed (exact)").get_data(as_text=True)


def test_a_work_without_a_doi_is_accepted_for_counts(client, lib):
    _run_search(client)
    html = client.get("/audit/review?cls=work without a DOI").get_data(as_text=True)
    assert "Accept ticked for counts" in html and "Commit ticked" not in html and "no DOI" in html

    html = client.post("/audit/accept-work", data=_form("Book1971", None, "W42", "", cls="work without a DOI", field="work")).get_data(as_text=True)
    assert "Accepted 1 works" in html and "accepted; its citation count is collected" in html
    assert journal.accepted_works(lib.config_path) == {"Book1971": "W42"}
    assert _disk(lib).loc["Book1971", "doi"] == ""

    client.post("/audit/reject", data=_form("Book1971", None, "W42", "", cls="work without a DOI", field="work"))
    assert journal.accepted_works(lib.config_path) == {}
    assert "Book1971" not in client.get("/audit/review?cls=work without a DOI").get_data(as_text=True)


def test_accept_work_is_admin_only_and_needs_a_tick(client):
    assert client.post("/audit/accept-work", environ_overrides={"REMOTE_ADDR": "10.8.0.1"}).status_code == 403
    assert "No rows ticked." in client.post("/audit/accept-work", data={"q": ""}).get_data(as_text=True)


# ---- the commands ----

def test_the_run_command_searches(lib, client, monkeypatch, capsys):
    from click.testing import CliRunner
    from archivum import cli as cli_module

    def run(command, *args):
        result = CliRunner().invoke(getattr(cli_module, command), list(args))
        return result.output + capsys.readouterr().out

    text = run("doi_audit_run", "--missing")
    assert "6 references in 6 to 12 requests" in text and "0 to 60 of the day's 10,000 credits" in text and "DRY RUN" in text
    assert client.transport.calls == [] and "DOI search: none." in run("doi_audit_status")

    text = run("doi_audit_run", "--missing", "-x")
    assert "finished" in text and "Something found for 5, nothing for 1" in text
    assert "DOI search: exact match 2, strong match 1, ambiguous 1, work without a doi 1, not found 1." in run("doi_audit_status")
    assert "Choose one thing to collect" in run("doi_audit_run", "--missing", "--counts")
