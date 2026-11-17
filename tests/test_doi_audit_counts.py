"""Citation-count runs: the journal, the runner and its lock, and the page. A fake transport stands in for OpenAlex."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading

import pandas as pd
import pytest

from archivum import egress
from archivum.doi_audit import journal, runner, sources

REFS = [
    {"tag": "Wang2020", "type": "article", "title": "One Work", "author": "Wang, Ruodu", "year": "2020", "doi": "10.1/One"},
    {"tag": "Wang2019", "type": "article", "title": "Two", "author": "Wang, Ruodu", "year": "2019", "doi": "https://doi.org/10.1/two"},
    {"tag": "Roe2018", "type": "book", "title": "Three", "author": "Roe, R.", "year": "2018", "doi": "10.1/ONE"},
    {"tag": "Poe2017", "type": "misc", "title": "Four", "author": "Poe, P.", "year": "2017", "doi": "see publisher"},
    {"tag": "Doe2016", "type": "article", "title": "Five", "author": "Doe, Jane", "year": "2016", "doi": None},
    {"tag": "Doe2015", "type": "article", "title": "Six", "author": "Doe, Jane", "year": "2015", "doi": "10.1/unknown"},
    {"tag": "Odd2014", "type": "article", "title": "Seven", "author": "Odd, O.", "year": "2014", "doi": "10.1/a,b"},
]
# what the fake OpenAlex has, by DOI in compared form
WORKS = {"10.1/one": ("W1", 120), "10.1/two": ("W2", 7), "10.1/a,b": ("W7", 3)}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code, self._payload = status_code, payload or {}
        self.headers = headers or {"X-RateLimit-Limit": "10000", "X-RateLimit-Remaining": "9000"}

    def json(self):
        return self._payload


def _work(doi):
    work_id, count = WORKS[doi]
    return {"id": f"https://openalex.org/{work_id}", "doi": f"https://doi.org/{doi}", "title": f"Title of {work_id}",
            "publication_year": 2020, "cited_by_count": count, "type": "article",
            "authorships": [{"author": {"display_name": "Ruodu Wang"}}],
            "primary_location": {"source": {"display_name": "J. One"}},
            "biblio": {"volume": "1", "issue": "2", "first_page": "3", "last_page": "9"}}


class OpenAlex:
    """A transport that answers as OpenAlex would for :data:`WORKS`, and records every call."""

    def __init__(self, fail_after=None, gate=None):
        self.calls, self.fail_after, self.gate = [], fail_after, gate

    def __call__(self, method, url, *, params, headers, timeout):
        self.calls.append((url, dict(params or {})))
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return FakeResponse(503)
        if url.endswith("/works"):
            dois = params["filter"].removeprefix("doi:").split("|")
            return FakeResponse(payload={"results": [_work(d) for d in dois if d in WORKS]})
        doi = url.rsplit("/works/doi:", 1)[1]
        return FakeResponse(payload=_work(doi)) if doi in WORKS else FakeResponse(404)


def _client(transport, mode=egress.SPEEDY):
    return egress.Egress(mode, contact_email="", transport=transport, sleep=lambda seconds: None)


@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


def _plan(lib, rows=None):
    return runner.plan_counts(lib.config_path, lib.ref_df if rows is None else rows, 30)


def _lines(lib, name):
    return journal.read_lines(lib.config_path, name)


def test_the_plan_counts_what_would_be_asked(lib):
    plan = _plan(lib)
    assert (plan.subset, plan.no_doi, plan.unusable, plan.fresh, plan.tags) == (7, 1, 1, 0, 5)
    # Wang2020 and Roe2018 share a DOI, compared without case, and are asked about once
    assert plan.work["10.1/one"] == ["Wang2020", "Roe2018"]
    assert plan.batches == [["10.1/one", "10.1/two", "10.1/unknown"]] and plan.singles == ["10.1/a,b"]
    assert plan.requests == 2


def test_batches_hold_at_most_a_hundred_dois(tmp_library):
    library = tmp_library([{"tag": f"T{i}", "type": "article", "doi": f"10.1/{i}"} for i in range(250)])
    plan = runner.plan_counts(library.config_path, library.ref_df, 30)
    assert [len(b) for b in plan.batches] == [100, 100, 50] and plan.requests == 3
    assert plan.seconds(egress.POLITE) == pytest.approx(3.0) and plan.seconds(egress.SPEEDY) == pytest.approx(1.5)


def test_a_run_appends_the_expected_lines_and_derives_cites(lib):
    transport = OpenAlex()
    state = runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(transport)).run()

    assert state["status"] == runner.FINISHED
    assert (state["total"], state["done"], state["found"], state["not_found"], state["errors"]) == (5, 5, 4, 1, 0)
    assert state["requests"] == 2 and len(transport.calls) == 2
    assert state["hosts"]["api.openalex.org"]["budget_remaining"] == 9000.0

    counts = {(c["tag"], c["doi"], c["count"], c["source_id"]) for c in _lines(lib, journal.COUNTS)}
    assert counts == {("Wang2020", "10.1/one", 120, "W1"), ("Roe2018", "10.1/one", 120, "W1"),
                      ("Wang2019", "10.1/two", 7, "W2"), ("Odd2014", "10.1/a,b", 3, "W7")}
    observations = {o["tag"]: o for o in _lines(lib, journal.OBSERVATIONS)}
    assert observations["Doe2015"]["status"] == journal.NOT_FOUND and observations["Doe2015"]["query"] == "10.1/unknown"
    hit = observations["Wang2020"]
    assert (hit["status"], hit["query_kind"], hit["first_author"], hit["container"], hit["pages"], hit["year"]) == (
        journal.OK, "doi", "Ruodu Wang", "J. One", "3--9", 2020)
    assert hit["run_id"] == state["run_id"] and hit["at"].endswith("+00:00")

    cites = journal.read_cites(lib.config_path).set_index("tag")
    assert list(cites.columns) == ["cites", "cites_asof", "cites_1y", "openalex_id", "cites_s2"]
    assert cites.loc["Wang2020", "cites"] == 120 and cites.loc["Odd2014", "openalex_id"] == "W7"
    assert cites["cites_1y"].isna().all() and "Doe2015" not in cites.index
    assert cites.loc["Wang2020", "cites_asof"] == dt.datetime.now(dt.timezone.utc).date().isoformat()


def test_only_dois_are_sent(lib):
    transport = OpenAlex()
    runner.CountsRun(lib.config_path, _plan(lib), egress.POLITE, client=_client(transport, egress.POLITE)).run()
    sent = json.dumps(transport.calls)
    assert all(url.startswith("https://api.openalex.org/works") for url, _ in transport.calls)
    assert "Wang" not in sent and "One Work" not in sent and "Wang2020" not in sent


def test_a_second_run_makes_no_requests(lib):
    runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex())).run()
    plan = _plan(lib)
    assert (plan.fresh, plan.tags, plan.requests) == (5, 0, 0)

    transport = OpenAlex()
    state = runner.CountsRun(lib.config_path, plan, egress.SPEEDY, client=_client(transport)).run()
    assert transport.calls == [] and state["status"] == runner.FINISHED


def test_a_stale_result_is_asked_again_and_a_changed_doi_too(lib):
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).isoformat(timespec="seconds")
    journal.append_lines(lib.config_path, journal.COUNTS, [
        {"at": old, "tag": "Wang2020", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 100},
        {"tag": "Wang2019", "source": "openalex", "source_id": "W0", "doi": "10.1/before-edit", "count": 1},
        {"tag": "Roe2018", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 119},
    ])
    plan = _plan(lib)
    assert plan.fresh == 1 and plan.work["10.1/one"] == ["Wang2020"] and "10.1/two" in plan.work


def test_a_run_stopped_part_way_resumes_without_repeating_finished_tags(tmp_library):
    library = tmp_library([{"tag": f"T{i:03d}", "type": "article", "doi": f"10.1/{i}"} for i in range(250)])
    plan = runner.plan_counts(library.config_path, library.ref_df, 30)
    transport = OpenAlex()
    client = _client(transport, egress.POLITE)
    run = runner.CountsRun(library.config_path, plan, egress.POLITE, client=client,
                           on_progress=lambda state: client.stop.set())  # stop after the first request
    state = run.run()

    assert state["status"] == runner.STOPPED and state["requests"] == 1 and state["done"] == 100
    assert not (journal.audit_dir(library.config_path) / runner.RUN_LOCK).exists()

    plan = runner.plan_counts(library.config_path, library.ref_df, 30)
    assert plan.fresh == 100 and plan.tags == 150
    second = OpenAlex()
    state = runner.CountsRun(library.config_path, plan, egress.POLITE, client=_client(second, egress.POLITE)).run()
    asked = {d for _, params in second.calls for d in params["filter"].removeprefix("doi:").split("|")}
    first = set(transport.calls[0][1]["filter"].removeprefix("doi:").split("|"))
    assert state["status"] == runner.FINISHED and len(asked) == 150 and not asked & first


def test_the_stop_file_stops_a_run_from_another_process(lib):
    plan = _plan(lib)
    transport = OpenAlex()
    run = runner.CountsRun(lib.config_path, plan, egress.POLITE, client=_client(transport, egress.POLITE))
    (journal.audit_dir(lib.config_path) / runner.RUN_STOP).write_text("now", encoding="utf-8")
    state = run.run()

    assert state["status"] == runner.STOPPED and transport.calls == []
    assert not (journal.audit_dir(lib.config_path) / runner.RUN_STOP).exists()


def test_a_second_runner_is_refused_by_the_lock(lib):
    gate = threading.Event()
    run = runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex(gate=gate)))
    thread = run.start()
    try:
        with pytest.raises(runner.RunActive, match="is active"):
            runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex()))
        assert runner.lock_holder(lib.config_path)["pid"] == os.getpid()
        assert runner.read_state(lib.config_path)["status"] == runner.RUNNING
    finally:
        gate.set()
        thread.join(10)
    assert runner.lock_holder(lib.config_path) is None
    assert runner.read_state(lib.config_path)["status"] == runner.FINISHED


def test_a_lock_left_by_a_dead_process_is_replaced(lib):
    folder = journal.audit_dir(lib.config_path)
    folder.mkdir(parents=True)
    (folder / runner.RUN_LOCK).write_text(json.dumps({"pid": 2 ** 22 + 12345, "run_id": "dead", "started": "then"}))
    (folder / runner.RUN_STATE).write_text(json.dumps({"status": runner.RUNNING, "run_id": "dead"}))

    assert runner.lock_holder(lib.config_path) is None
    assert runner.read_state(lib.config_path)["status"] == runner.FAILED
    assert runner.request_stop(lib.config_path) is False
    state = runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex())).run()
    assert state["status"] == runner.FINISHED


def test_a_host_that_keeps_failing_ends_the_run_and_says_so(tmp_library):
    library = tmp_library([{"tag": f"T{i:03d}", "type": "article", "doi": f"10.1/{i}"} for i in range(250)])
    plan = runner.plan_counts(library.config_path, library.ref_df, 30)
    transport = OpenAlex(fail_after=1)
    state = runner.CountsRun(library.config_path, plan, egress.POLITE, client=_client(transport, egress.POLITE)).run()

    assert state["status"] == runner.STOPPED and "5 failures in a row" in state["message"]
    assert state["requests"] == 1 and len(transport.calls) == 1 + egress.FAILURES_TO_STOP_HOST
    assert "5 failures" in state["hosts"]["api.openalex.org"]["stopped"]


def test_an_error_status_is_recorded_and_the_run_goes_on(lib, monkeypatch):
    def refuse(client, dois):
        raise sources.SourceError("OpenAlex answered HTTP 400")

    monkeypatch.setattr(sources, "openalex_works_by_doi", refuse)
    state = runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex())).run()

    assert state["status"] == runner.FINISHED and state["errors"] == 4 and state["found"] == 1
    errors = [o for o in _lines(lib, journal.OBSERVATIONS) if o["status"] == journal.ERROR]
    assert len(errors) == 4 and "HTTP 400" in errors[0]["error"]
    # an error is not a result: the references are asked about again next time
    assert _plan(lib).tags == 4


def test_a_write_in_the_audit_folder_does_not_ask_for_a_reload(lib):
    lib.start_watcher()
    try:
        runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex())).run()
        deadline = dt.datetime.now() + dt.timedelta(seconds=1.5)
        while dt.datetime.now() < deadline:
            assert lib.needs_reload is False
            threading.Event().wait(0.1)
    finally:
        lib._observer.stop()
        lib._observer.join()
    before = pd.read_feather(lib.config_path / "ref.feather")
    assert before.equals(pd.DataFrame(REFS).astype(before.dtypes.to_dict()))


def test_growth_over_a_year_needs_a_count_a_year_old(lib):
    now = dt.datetime.now(dt.timezone.utc)

    def at(days):
        return (now - dt.timedelta(days=days)).isoformat(timespec="seconds")

    journal.append_lines(lib.config_path, journal.COUNTS, [
        {"at": at(800), "tag": "Wang2020", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 50},
        {"at": at(400), "tag": "Wang2020", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 90},
        {"at": at(100), "tag": "Wang2020", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 110},
        {"at": at(0), "tag": "Wang2020", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 120},
        {"at": at(200), "tag": "Wang2019", "source": "openalex", "source_id": "W2", "doi": "10.1/two", "count": 5},
        {"at": at(0), "tag": "Wang2019", "source": "openalex", "source_id": "W2", "doi": "10.1/two", "count": 7},
    ])
    cites = journal.derive_cites(lib.config_path).set_index("tag")

    assert cites.loc["Wang2020", "cites"] == 120 and cites.loc["Wang2020", "cites_1y"] == 30
    assert cites.loc["Wang2019", "cites"] == 7 and pd.isna(cites.loc["Wang2019", "cites_1y"])


def test_a_line_cut_short_by_a_crash_is_skipped(lib):
    journal.append_lines(lib.config_path, journal.COUNTS, [
        {"tag": "Wang2020", "source": "openalex", "source_id": "W1", "doi": "10.1/one", "count": 120}])
    with open(journal.audit_dir(lib.config_path) / journal.COUNTS, "a", encoding="utf-8") as f:
        f.write('{"tag": "Wang2019", "sour')
    assert len(_lines(lib, journal.COUNTS)) == 1


def test_durations_read_plainly():
    assert [runner.format_duration(s) for s in (0.2, 31, 200, 5400, 7500)] == ["1 s", "31 s", "3 min", "1 h 30 min", "2 h 05 min"]


# ---- the page ----

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


@pytest.fixture()
def fake_openalex(monkeypatch):
    """Make every new ``Egress`` use a fake OpenAlex; returns the transport."""
    transport = OpenAlex()
    real = egress.Egress

    def make(mode=egress.SPEEDY, **kwargs):
        return real(mode, contact_email="", transport=transport, sleep=lambda seconds: None, stop=kwargs.get("stop"))

    monkeypatch.setattr(egress, "Egress", make)
    return transport


def _wait_for_run(lib):
    for thread in threading.enumerate():
        if thread.name.startswith("doi-audit-run-"):
            thread.join(10)
    assert runner.lock_holder(lib.config_path) is None


@pytest.mark.parametrize("method, path", [
    ("get", "/audit/plan"), ("get", "/audit/run-status"), ("post", "/audit/run"), ("post", "/audit/stop"),
])
def test_a_visitor_from_the_bridge_is_refused(client, method, path):
    assert getattr(client, method)(path, environ_overrides={"REMOTE_ADDR": "10.8.0.1"}).status_code == 403


def test_the_plan_is_shown_before_anything_is_sent(client, fake_openalex):
    html = client.get("/audit/plan").get_data(as_text=True)
    assert "<strong>5</strong> would be asked about" in html and "<strong>2</strong> requests" in html
    assert "1 with no DOI, 1 whose DOI needs attention" in html
    assert 'value="speedy" checked' in html and "Start run" in html
    assert fake_openalex.calls == []


def test_start_runs_in_the_background_and_the_counts_table_follows(client, lib, fake_openalex):
    html = client.post("/audit/run", data={"q": "", "mode": "polite"}).get_data(as_text=True)
    assert "Run started: 5 references in 2 requests, polite." in html
    _wait_for_run(lib)

    response = client.get("/audit/run-status")
    status = response.get_data(as_text=True)
    assert json.loads(response.headers["HX-Trigger"]) == {"audit-run-ended": {"cls": "counts"}}
    assert "Last count run finished" in status and "Found 4, not at OpenAlex 1" in status
    assert "every 5s" not in status and "credits left today: 9,000 of 10,000" in status
    assert 'id="audit-summary" hx-swap-oob="true"' in status

    table = client.get("/audit/review?cls=counts").get_data(as_text=True)
    assert "Citation counts: 4 references" in table
    assert table.index("Wang2020") < table.index("Wang2019") < table.index("Odd2014")
    assert "Growth" not in table and 'text-nowrap pe-3">As of' in table
    summary = client.get("/audit/subset?q=author ~ /Wang/").get_data(as_text=True)
    assert "With a count" in summary and "Last count run finished" in summary


def test_the_status_line_polls_only_while_a_run_is_active(client, lib, monkeypatch):
    gate = threading.Event()
    transport = OpenAlex(gate=gate)
    real = egress.Egress
    monkeypatch.setattr(egress, "Egress", lambda mode=egress.SPEEDY, **kw: real(
        mode, contact_email="", transport=transport, sleep=lambda s: None, stop=kw.get("stop")))
    try:
        assert "every 5s" not in client.get("/audit/run-status").get_data(as_text=True)
        client.post("/audit/run", data={"q": "", "mode": "speedy"})
        response = client.get("/audit/run-status")
        active = response.get_data(as_text=True)
        assert "HX-Trigger" not in response.headers
        assert "every 5s" in active and "Stop run" in active and "audit-summary" not in active
        again = client.post("/audit/run", data={"q": "", "mode": "speedy"}).get_data(as_text=True)
        assert "Not started." in again and "is active" in again
        assert "Stop requested" in client.post("/audit/stop", data={"q": ""}).get_data(as_text=True)
    finally:
        gate.set()
    _wait_for_run(lib)
    assert "Last count run stopped" in client.get("/audit/run-status").get_data(as_text=True)
    assert "No run is active." in client.post("/audit/stop", data={"q": ""}).get_data(as_text=True)


def test_start_with_nothing_to_fetch_or_no_mode(client, lib, fake_openalex):
    assert "Choose speedy or polite." in client.post("/audit/run", data={"q": ""}).get_data(as_text=True)
    client.post("/audit/run", data={"q": "", "mode": "speedy"})
    _wait_for_run(lib)
    calls = len(fake_openalex.calls)
    html = client.post("/audit/run", data={"q": "", "mode": "speedy"}).get_data(as_text=True)
    assert "Nothing to ask" in html and len(fake_openalex.calls) == calls


# ---- the commands ----

@pytest.fixture()
def cli(lib, monkeypatch, capsys):
    from click.testing import CliRunner
    from archivum import cli as cli_module

    monkeypatch.setattr(cli_module.LibraryContext, "get", classmethod(lambda cls: lib))

    def run(command, *args):
        result = CliRunner().invoke(getattr(cli_module, command), list(args))
        return result.exit_code, result.output + capsys.readouterr().out

    return run


def test_the_run_command_is_a_dry_run_without_x(cli, lib, fake_openalex):
    code, text = cli("doi_audit_run", "--counts")
    assert code == 0 and "5 references in 2 requests" in text and "DRY RUN" in text and "speedy" in text
    assert fake_openalex.calls == [] and _lines(lib, journal.COUNTS) == []

    code, text = cli("doi_audit_run", "author ~ /Wang/", "--counts", "--mode", "polite", "-x")
    assert code == 0 and "finished" in text and "Found 2, not at OpenAlex 0" in text
    assert {c["tag"] for c in _lines(lib, journal.COUNTS)} == {"Wang2020", "Wang2019"}

    code, text = cli("doi_audit_status")
    assert "finished" in text and "Citation counts: 2 references" in text


def test_the_run_command_needs_an_action_and_a_query_that_parses(cli, fake_openalex):
    assert "Choose one thing to collect" in cli("doi_audit_run")[1]
    assert "Query not understood" in cli("doi_audit_run", "year == 2020", "--counts")[1]
    assert "No run yet." in cli("doi_audit_status")[1]


# ---- a second count from Semantic Scholar ----

class TwoSources(OpenAlex):
    """OpenAlex as above, and Semantic Scholar's batch endpoint with counts for two of the DOIs."""

    SECOND = {"10.1/one": ("s2-one", 131), "10.1/two": ("s2-two", 9)}

    def __call__(self, method, url, *, params, headers, timeout, json=None):
        if "semanticscholar" in url:
            self.calls.append((url, dict(params or {})))
            self.second_ids = json["ids"]
            return FakeResponse(payload=[
                {"paperId": self.SECOND[i.removeprefix("DOI:")][0], "citationCount": self.SECOND[i.removeprefix("DOI:")][1]}
                if i.removeprefix("DOI:") in self.SECOND else None for i in json["ids"]])
        return super().__call__(method, url, params=params, headers=headers, timeout=timeout)


def test_a_second_count_is_planned_only_when_asked_for(lib):
    assert _plan(lib).second == {} and _plan(lib).requests == 2
    plan = runner.plan_counts(lib.config_path, lib.ref_df, 30, second=True)
    assert set(plan.second) == {"10.1/one", "10.1/two", "10.1/unknown", "10.1/a,b"}
    assert plan.requests == 3 and plan.tags == 5


def test_a_second_count_sits_beside_the_openalex_one(lib):
    transport = TwoSources()
    plan = runner.plan_counts(lib.config_path, lib.ref_df, 30, second=True)
    state = runner.CountsRun(lib.config_path, plan, egress.SPEEDY, client=_client(transport)).run()

    assert state["status"] == runner.FINISHED and state["requests"] == 3 and state["done"] == 5
    assert sorted(transport.second_ids) == ["DOI:10.1/a,b", "DOI:10.1/one", "DOI:10.1/two", "DOI:10.1/unknown"]
    cites = journal.read_cites(lib.config_path).set_index("tag")
    assert (cites.loc["Wang2020", "cites"], cites.loc["Wang2020", "cites_s2"]) == (120, 131)
    assert cites.loc["Roe2018", "cites_s2"] == 131 and cites.loc["Wang2019", "cites_s2"] == 9
    assert pd.isna(cites.loc["Odd2014", "cites_s2"])
    # the two sources are fresh separately
    again = runner.plan_counts(lib.config_path, lib.ref_df, 30, second=True)
    assert again.second == {} and again.work == {} and again.requests == 0


def test_a_second_count_alone_can_be_added_later(lib):
    runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex())).run()
    plan = runner.plan_counts(lib.config_path, lib.ref_df, 30, second=True)
    assert plan.work == {} and len(plan.second) == 4 and plan.requests == 1 and plan.tags == 5

    transport = TwoSources()
    state = runner.CountsRun(lib.config_path, plan, egress.SPEEDY, client=_client(transport)).run()
    assert state["done"] == 5 and [url for url, _ in transport.calls] == [
        "https://api.semanticscholar.org/graph/v1/paper/batch"]
    assert journal.read_cites(lib.config_path).set_index("tag").loc["Wang2019", "cites_s2"] == 9


def test_a_cites_file_from_before_the_second_count_still_reads(lib):
    runner.CountsRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(OpenAlex())).run()
    path = journal.audit_dir(lib.config_path) / journal.CITES
    pd.read_feather(path).drop(columns=["cites_s2"]).to_feather(path)

    cites = journal.read_cites(lib.config_path)
    assert "cites_s2" in cites.columns and cites["cites_s2"].isna().all()
    assert lib.database.set_index("tag").loc["Wang2020", "cites"] == 120


def test_the_page_offers_the_second_count_and_shows_its_column(client, lib, monkeypatch):
    transport = TwoSources()
    real = egress.Egress
    monkeypatch.setattr(egress, "Egress", lambda mode=egress.SPEEDY, **kw: real(
        mode, contact_email="", transport=transport, sleep=lambda s: None, stop=kw.get("stop")))

    plan = " ".join(client.get("/audit/plan").get_data(as_text=True).split())
    assert 'name="second"' in plan and "4 DOIs in 1 more request" in plan
    client.post("/audit/run", data={"q": "", "mode": "speedy"})
    _wait_for_run(lib)
    assert "Semantic Scholar" not in client.get("/audit/review?cls=counts").get_data(as_text=True)

    html = client.post("/audit/run", data={"q": "", "mode": "speedy", "second": "1"}).get_data(as_text=True)
    assert "Run started: 5 references in 1 requests" in html
    _wait_for_run(lib)
    table = client.get("/audit/review?cls=counts").get_data(as_text=True)
    assert "Semantic Scholar</th>" in table and ">131<" in table
