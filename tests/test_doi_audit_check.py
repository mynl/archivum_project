"""The DOI check: the match score, the staged run, and the review. Fake OpenAlex, Crossref and doi.org."""

from __future__ import annotations

import json
import threading

import pandas as pd
import pytest

from archivum import egress
from archivum.doi_audit import doi as rules
from archivum.doi_audit import journal, runner
from archivum.doi_audit.proposals import check_counts, checked_dois

REFS = [
    {"tag": "Good2020", "type": "article", "title": "Coherent Measures of {R}isk", "author": "Artzner, Philippe and Delbaen, F.", "year": "2020", "doi": "10.1/good"},
    {"tag": "Year2015", "type": "article", "title": "On the Year", "author": "Roe, R.", "year": "2015", "doi": "10.1/year"},
    {"tag": "Wrong2018", "type": "article", "title": "Capital Allocation by Percentile Layer", "author": "Bodoff, N.", "year": "2018", "doi": "10.1/wrong"},
    {"tag": "Weak2017", "type": "article", "title": "Insurance Risk and Ruin", "author": "Dickson, David", "year": "2017", "doi": "10.1/weak"},
    {"tag": "Data2016", "type": "misc", "title": "A Data Set", "author": "Poe, P.", "year": "2016", "doi": "10.5281/zenodo.1"},
    {"tag": "Gone2014", "type": "article", "title": "Gone", "author": "Doe, Jane", "year": "2014", "doi": "10.1/gone"},
    {"tag": "Share2013a", "type": "article", "title": "Shared Work", "author": "Wang, R.", "year": "2013", "doi": "10.1/Share"},
    {"tag": "Share2013b", "type": "article", "title": "Shared Work", "author": "Wang, R.", "year": "2013", "doi": "10.1/share"},
    {"tag": "NoDoi2012", "type": "article", "title": "None", "author": "Doe, Jane", "year": "2012", "doi": None},
    {"tag": "Bad2011", "type": "article", "title": "Bad", "author": "Doe, Jane", "year": "2011", "doi": "see publisher"},
]
OPENALEX = {
    "10.1/good": ("Coherent measures of risk", "Philippe Artzner", 2020, 900),
    "10.1/year": ("On the year", "R. Roe", 2019, 4),
    "10.1/wrong": ("A completely different paper about fish", "A. Fisher", 2018, 2),
    "10.1/weak": ("Insurance risk and ruin, with an appendix on stochastic processes", "David C. M. Dickson", 2017, 50),
    "10.1/share": ("Shared work", "Ruodu Wang", 2013, 11),
}
CROSSREF = {
    "10.1/wrong": ("A completely different paper about fish", ("A.", "Fisher"), 2018),
    "10.1/weak": ("Insurance Risk and Ruin", ("David", "Dickson"), 2017),
}
REGISTERED_ELSEWHERE = {"10.5281/zenodo.1"}


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code, self._payload, self.headers = status_code, payload or {}, {}

    def json(self):
        return self._payload


class Sources:
    """A transport answering for the three sources, recording every call by host."""

    def __init__(self):
        self.calls = []

    def hosts(self):
        return [url.split("/")[2] for _, url in self.calls]

    def __call__(self, method, url, *, params, headers, timeout):
        self.calls.append((method, url))
        if url.startswith("https://api.openalex.org/works"):
            dois = params["filter"].removeprefix("doi:").split("|")
            return FakeResponse(payload={"results": [
                {"id": f"https://openalex.org/W{n}", "doi": f"https://doi.org/{d}", "title": OPENALEX[d][0],
                 "publication_year": OPENALEX[d][2], "cited_by_count": OPENALEX[d][3], "type": "article",
                 "authorships": [{"author": {"display_name": OPENALEX[d][1]}}],
                 "primary_location": {"source": {"display_name": "J."}}, "biblio": {}}
                for n, d in enumerate(dois) if d in OPENALEX]})
        if url.startswith("https://api.crossref.org/works/"):
            doi = url.removeprefix("https://api.crossref.org/works/")
            if doi not in CROSSREF:
                return FakeResponse(404)
            title, (given, family), year = CROSSREF[doi]
            return FakeResponse(payload={"message": {
                "DOI": doi, "title": [title], "author": [{"given": given, "family": family}],
                "issued": {"date-parts": [[year, 3]]}, "container-title": ["J."], "type": "journal-article"}})
        doi = url.removeprefix("https://doi.org/")
        return FakeResponse(302 if doi in OPENALEX or doi in CROSSREF or doi in REGISTERED_ELSEWHERE else 404)


def _client(transport, mode=egress.SPEEDY):
    return egress.Egress(mode, contact_email="", transport=transport, sleep=lambda seconds: None)


# ---- the score ----

def test_fold_drops_latex_html_accents_and_punctuation():
    assert rules.fold('{\\"U}ber die <i>Sache</i> &amp; mehr: Teil 2') == "uber die sache mehr teil 2"
    assert rules.fold("Coherent Measures of {R}isk") == rules.fold("coherent measures of risk.")
    assert rules.fold(None) == ""


def test_title_similarity():
    assert rules.title_similarity("Coherent Measures of {R}isk", "Coherent measures of risk") == 1.0
    assert rules.title_similarity("Insurance Risk and Ruin", "Capital Allocation") < 0.6
    assert 0.6 < rules.title_similarity("Insurance Risk and Ruin", "Insurance risk and ruin, second edition") < 0.9
    assert rules.title_similarity("", "Anything") == 0.0


@pytest.mark.parametrize("library, source, agree", [
    ("Wang, Ruodu and Zitikis, R.", "Ruodu Wang", True),
    ("Wang, R.", "R. Wang", True),
    ("van der Vaart, A. W.", "A. W. van der Vaart", True),
    ("Embrechts, Paul", "Alexander J. McNeil", False),
    ("M{\\\"u}ller, Alfred", "Alfred Müller", True),
    ("{Casualty Actuarial Society}", "Casualty Actuarial Society", True),
    ("Ruodu Wang", "Wang Ruodu", True),
    ("Wang, Ruodu", None, None),
    (None, "Ruodu Wang", None),
])
def test_first_author_surnames(library, source, agree):
    assert rules.surnames_agree(library, source) is agree


@pytest.mark.parametrize("title, surname, years, cls", [
    (1.0, True, 0, rules.VERIFIED),
    (0.9, True, 1, rules.VERIFIED),
    (0.95, None, None, rules.VERIFIED),
    (1.0, True, 2, rules.YEAR_DIFFERS),
    (0.89, True, 0, rules.WEAK_MATCH),
    (0.6, True, 0, rules.WEAK_MATCH),
    (0.59, True, 0, rules.MISMATCH),
    (1.0, False, 0, rules.MISMATCH),
])
def test_classes_from_a_match(title, surname, years, cls):
    assert rules.Match(title, surname, years).cls == cls


# ---- the run ----

@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


def _plan(lib):
    return runner.plan_check(lib.config_path, lib.ref_df, 30)


def _classes(lib):
    checked = checked_dois(lib.ref_df, lib.ref_df, journal.latest_observations(lib.config_path))
    return {p.tag: p for p in checked}


def test_the_plan_starts_everything_at_openalex(lib):
    plan = _plan(lib)
    assert (plan.subset, plan.no_doi, plan.unusable, plan.fresh, plan.tags) == (10, 1, 1, 0, 8)
    assert set(plan.stages.values()) == {runner.OPENALEX_STAGE}
    # 7 distinct DOIs: one OpenAlex request at least, and at most two more for each
    assert (plan.openalex_requests, plan.requests, plan.requests_most) == (1, 1, 15)


def test_a_check_asks_each_source_only_where_the_one_before_did_not_settle(lib):
    transport = Sources()
    state = runner.CheckRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(transport)).run()

    assert state["status"] == runner.FINISHED and state["action"] == "check"
    assert (state["total"], state["done"], state["errors"]) == (8, 8, 0)
    hosts = transport.hosts()
    assert hosts.count("api.openalex.org") == 1
    crossref = sorted(url.rsplit("/works/", 1)[1] for _, url in transport.calls if "crossref" in url)
    assert crossref == ["10.1/gone", "10.1/weak", "10.1/wrong", "10.5281/zenodo.1"]
    doi_org = sorted(url.removeprefix("https://doi.org/") for method, url in transport.calls if url.startswith("https://doi.org/"))
    assert doi_org == ["10.1/gone", "10.5281/zenodo.1"]
    assert all(method == "HEAD" for method, url in transport.calls if url.startswith("https://doi.org/"))
    assert state["requests"] == 7 == len(transport.calls)


def test_classes_after_a_check(lib):
    runner.CheckRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(Sources())).run()
    rows = _classes(lib)

    assert rows["Good2020"].cls == rules.VERIFIED and rows["Good2020"].score == 1.0
    assert rows["Year2015"].cls == rules.YEAR_DIFFERS
    assert rows["Wrong2018"].cls == rules.MISMATCH and rows["Wrong2018"].record["author"] in ("A. Fisher",)
    # OpenAlex's title is only close; Crossref's is exact, and the better record is the one used
    assert rows["Weak2017"].cls == rules.VERIFIED and rows["Weak2017"].source == "crossref"
    assert rows["Gone2014"].cls == rules.UNRESOLVED
    assert rows["Share2013a"].cls == rules.VERIFIED and rows["Share2013a"].shared_with == ("Share2013b",)
    assert "NoDoi2012" not in rows and "Bad2011" not in rows
    assert check_counts(list(rows.values())) == {
        rules.UNRESOLVED: 1, rules.MISMATCH: 1, rules.WEAK_MATCH: 0, rules.YEAR_DIFFERS: 1, rules.SHARED: 2,
        rules.NOT_COMPARED: 1, rules.VERIFIED: 4, rules.NOT_CHECKED: 0}


def test_a_doi_registered_outside_crossref_is_not_unresolved(lib):
    runner.CheckRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(Sources())).run()
    row = _classes(lib)["Data2016"]
    assert row.cls == rules.NOT_COMPARED and row.proposed is None


def test_a_check_collects_the_citation_counts_too(lib):
    runner.CheckRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(Sources())).run()
    cites = journal.read_cites(lib.config_path).set_index("tag")
    assert cites.loc["Good2020", "cites"] == 900 and cites.loc["Share2013b", "cites"] == 11
    assert "Gone2014" not in cites.index


def test_a_second_check_asks_nothing(lib):
    runner.CheckRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(Sources())).run()
    plan = _plan(lib)
    assert (plan.fresh, plan.tags, plan.requests, plan.requests_most) == (8, 0, 0, 0)


def test_a_count_run_first_saves_the_check_its_openalex_requests(lib):
    counts = runner.plan_counts(lib.config_path, lib.ref_df, 30)
    runner.CountsRun(lib.config_path, counts, egress.SPEEDY, client=_client(Sources())).run()
    plan = _plan(lib)

    # the verified ones are settled already; the rest go straight to Crossref
    assert plan.fresh == 4 and plan.openalex_requests == 0
    assert set(plan.stages.values()) == {runner.CROSSREF_STAGE} and plan.requests == 4
    transport = Sources()
    runner.CheckRun(lib.config_path, plan, egress.SPEEDY, client=_client(transport)).run()
    assert "api.openalex.org" not in transport.hosts()
    assert _classes(lib)["Gone2014"].cls == rules.UNRESOLVED


def test_a_check_stopped_after_openalex_resumes_at_crossref(lib):
    transport = Sources()
    client = _client(transport, egress.POLITE)
    state = runner.CheckRun(lib.config_path, _plan(lib), egress.POLITE, client=client,
                            on_progress=lambda s: client.stop.set()).run()
    assert state["status"] == runner.STOPPED and transport.hosts() == ["api.openalex.org"]
    # an OpenAlex not-found alone settles nothing
    assert _classes(lib)["Gone2014"].cls == rules.NOT_CHECKED

    plan = _plan(lib)
    assert plan.fresh == 4 and set(plan.stages.values()) == {runner.CROSSREF_STAGE}


def test_the_references_are_not_changed_by_a_check(lib):
    before = (lib.config_path / "ref.feather").read_bytes()
    runner.CheckRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(Sources())).run()
    assert (lib.config_path / "ref.feather").read_bytes() == before


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


def _run_check(client, lib):
    html = client.post("/audit/run", data={"q": "", "action": "check", "mode": "speedy"}).get_data(as_text=True)
    for thread in threading.enumerate():
        if thread.name.startswith("doi-audit-run-"):
            thread.join(10)
    return html


def _form(tag, current, new, cls="unresolved"):
    key = f"{tag}|doi"
    return {"q": "", "cls": cls, "page": "1", "row": [key], f"old::{key}": json.dumps(current),
            f"proposed::{key}": "", f"new::{key}": new}


def _disk(lib):
    return pd.read_feather(lib.config_path / "ref.feather").set_index("tag")


def test_the_check_plan_gives_a_range_and_sends_nothing(client):
    html = client.get("/audit/plan?action=check").get_data(as_text=True)
    assert "<strong>8</strong> would be checked" in html and "<strong>1 to 15</strong> requests" in html
    assert 'name="action" value="check"' in html and client.transport.calls == []


def test_a_check_from_the_page_then_its_results(client, lib):
    assert "DOI check started: 8 references, 1 requests or more, speedy." in _run_check(client, lib)

    response = client.get("/audit/run-status")
    assert json.loads(response.headers["HX-Trigger"]) == {"audit-run-ended": {"cls": "check"}}
    assert "Last DOI check finished" in response.get_data(as_text=True)

    html = client.get("/audit/review?cls=check").get_data(as_text=True)
    # opens on the first class with rows
    assert "DOI check, Unresolved: 1 row<" in html and "Gone2014" in html
    for pill in ("Unresolved 1", "Mismatch 1", "Year differs 1", "Shared 2", "Exists, not compared 1", "Verified 4"):
        assert pill in html
    assert 'placeholder="empty clears the DOI"' in html and "Commit ticked" in html
    assert ">8</div>" in html and "DOIs checked" in html


def test_information_classes_have_no_tick_box(client, lib):
    _run_check(client, lib)
    for cls in ("year differs", "shared", "not compared", "verified"):
        html = client.get(f"/audit/review?cls={cls}").get_data(as_text=True)
        assert 'name="row"' not in html and "Commit ticked" not in html
    shared = client.get("/audit/review?cls=shared").get_data(as_text=True)
    assert "Same DOI as" in shared and 'href="/edit?tag=Share2013b"' in shared
    mismatch = client.get("/audit/review?cls=mismatch").get_data(as_text=True)
    assert "A completely different paper about fish" in mismatch and "Capital Allocation by Percentile Layer" in mismatch
    assert "A. Fisher" in mismatch and 'name="row" value="Wrong2018|doi"' in mismatch


def test_a_replacement_doi_is_committed_and_is_then_unchecked(client, lib):
    _run_check(client, lib)
    html = client.post("/audit/commit", data=_form("Gone2014", "10.1/gone", "10.1/found")).get_data(as_text=True)

    assert "Committed 1 of 1 rows" in html and _disk(lib).loc["Gone2014", "doi"] == "10.1/found"
    assert "Not checked 1" in client.get("/audit/review?cls=check").get_data(as_text=True)


def test_an_empty_replacement_clears_the_doi(client, lib):
    _run_check(client, lib)
    client.post("/audit/commit", data=_form("Gone2014", "10.1/gone", ""))
    assert _disk(lib).loc["Gone2014", "doi"] == ""
    assert "Gone2014" not in client.get("/audit/review?cls=unresolved").get_data(as_text=True)


def test_a_rejected_mismatch_is_not_listed_again(client, lib):
    _run_check(client, lib)
    html = client.post("/audit/reject", data=_form("Wrong2018", "10.1/wrong", "", cls="mismatch")).get_data(as_text=True)

    assert "Rejected 1 rows" in html and "Mismatch 0" in html
    assert _disk(lib).loc["Wrong2018", "doi"] == "10.1/wrong"


# ---- the commands ----

@pytest.fixture()
def cli(lib, client, monkeypatch, capsys):
    from click.testing import CliRunner
    from archivum import cli as cli_module

    def run(command, *args):
        result = CliRunner().invoke(getattr(cli_module, command), list(args))
        return result.exit_code, result.output + capsys.readouterr().out

    return run


def test_the_run_command_checks_dois(cli, lib, client):
    code, text = cli("doi_audit_run", "--dois")
    assert code == 0 and "8 references in 1 to 15 requests" in text and "DRY RUN" in text
    assert client.transport.calls == []
    assert "DOI check: none." in cli("doi_audit_status")[1]

    code, text = cli("doi_audit_run", "--dois", "-x")
    assert code == 0 and "finished" in text and len(client.transport.calls) == 7

    code, text = cli("doi_audit_status")
    assert "DOI check: unresolved 1, mismatch 1, year differs 1, shared 2, exists, not compared 1, verified 4." in text
    assert "Title similarity of 6 compared (thresholds 0.6 and 0.9)" in text and "1.00 5" in text
