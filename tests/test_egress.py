"""``archivum.egress``: the allow-list, pacing, backoff and budget stop. No test sends a request."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
import requests

from archivum import egress
from archivum.egress import Egress

SRC = Path(__file__).resolve().parents[1] / "src" / "archivum"


class FakeResponse:
    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code, self.headers, self._payload = status_code, headers or {}, payload or {}

    def json(self):
        return self._payload


class FakeClock:
    """Time that moves only when something sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Transport:
    """Returns the queued responses in order, then 200s; records every call."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, *, params, headers, timeout):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers, "timeout": timeout})
        item = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


def make(mode=egress.SPEEDY, *responses, email="me@example.org"):
    clock, transport = FakeClock(), Transport(*responses)
    return Egress(mode, contact_email=email, transport=transport, clock=clock, sleep=clock.sleep), transport, clock


@pytest.mark.parametrize("url", [
    "https://example.org/works",
    "https://api.crossref.org.example.org/works",
    "http://api.crossref.org/works",
    "https://user@evil.example/api.crossref.org",
    "ftp://doi.org/10.1/x",
    "not a url",
])
def test_a_host_outside_the_list_is_refused_and_nothing_is_sent(url):
    client, transport, _ = make()
    with pytest.raises(egress.EgressRefused):
        client.get(url)
    assert transport.calls == []


@pytest.mark.parametrize("host", sorted(egress.ALLOWED_HOSTS))
def test_each_listed_host_is_allowed(host):
    client, transport, _ = make()
    assert client.get(f"https://{host}/x").status_code == 200
    assert len(transport.calls) == 1


def test_the_allow_list_is_the_five_sources():
    assert egress.ALLOWED_HOSTS == {"api.openalex.org", "api.crossref.org", "doi.org", "export.arxiv.org",
                                    "api.semanticscholar.org"}


def test_requests_is_used_only_in_egress_and_the_ingest_download():
    pattern = re.compile(r"^\s*(import requests\b|from requests\b)|\brequests\.(get|post|put|head|request|Session)\(", re.M)
    users = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))}
    assert users == {"egress.py", "web/routes/ingest.py"}


def test_identity_goes_to_crossref_and_openalex_only(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k-123")
    client, transport, _ = make()
    client.get("https://api.crossref.org/works", params={"rows": 1})
    client.get("https://api.openalex.org/works")
    client.get("https://doi.org/10.1/x")

    crossref, openalex, doi = transport.calls
    assert crossref["params"] == {"rows": 1, "mailto": "me@example.org"}
    assert openalex["params"] == {"mailto": "me@example.org", "api_key": "k-123"}
    assert doi["params"] is None
    assert all("mailto:me@example.org" in c["headers"]["User-Agent"] for c in transport.calls)
    assert all(c["timeout"] == egress.DEFAULT_TIMEOUT for c in transport.calls)


def test_the_semantic_scholar_key_goes_to_that_host_only_and_a_body_is_passed_on(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "s2-key")
    seen = []

    def transport(method, url, *, params, headers, timeout, json=None):
        seen.append((url, headers, json))
        return FakeResponse()

    client = Egress(egress.SPEEDY, contact_email="", transport=transport, sleep=lambda s: None)
    client.post("https://api.semanticscholar.org/graph/v1/paper/batch", json={"ids": ["DOI:10.1/x"]})
    client.get("https://api.crossref.org/works")

    assert seen[0][1]["x-api-key"] == "s2-key" and seen[0][2] == {"ids": ["DOI:10.1/x"]}
    assert "x-api-key" not in seen[1][1] and seen[1][2] is None


def test_no_email_sends_no_mailto(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    client, transport, _ = make(email="")
    client.get("https://api.openalex.org/works")
    assert transport.calls[0]["params"] is None and "mailto" not in transport.calls[0]["headers"]["User-Agent"]


@pytest.mark.parametrize("mode, kind, interval", [
    (egress.SPEEDY, egress.SINGLE, 0.2), (egress.SPEEDY, egress.SEARCH, 0.5),
    (egress.POLITE, egress.SINGLE, 1.0), (egress.POLITE, egress.SEARCH, 1.0),
])
def test_requests_to_a_host_start_one_interval_apart(mode, kind, interval):
    client, _, clock = make(mode)
    for _ in range(4):
        client.get("https://api.crossref.org/works", kind=kind)
    assert clock.sleeps == pytest.approx([interval] * 3)


def test_hosts_are_paced_separately_and_arxiv_is_three_seconds_in_every_mode():
    client, _, clock = make(egress.SPEEDY)
    client.get("https://api.crossref.org/works")
    client.get("https://api.openalex.org/works")
    assert clock.sleeps == []
    client.get("https://export.arxiv.org/api/query")
    client.get("https://export.arxiv.org/api/query")
    assert clock.sleeps == pytest.approx([3.0])


def test_threads_sharing_one_egress_cannot_exceed_the_rate():
    """With real threads the start times handed out are still one interval apart."""
    clock, starts, lock = FakeClock(), [], threading.Lock()

    def transport(method, url, *, params, headers, timeout):
        return FakeResponse()

    def sleep(seconds):
        with lock:
            starts.append(seconds)

    client = Egress(egress.SPEEDY, contact_email="", transport=transport, clock=clock, sleep=sleep)
    threads = [threading.Thread(target=client.get, args=("https://api.crossref.org/works",)) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()

    # the clock never moves, so the waits are the offsets of the start times: 0.2, 0.4, ...
    assert sorted(starts) == pytest.approx([0.2, 0.4, 0.6, 0.8, 1.0])


def test_concurrency_is_bounded_per_host():
    assert egress.MODES[egress.SPEEDY].concurrency == 3 and egress.MODES[egress.POLITE].concurrency == 1
    client, _, _ = make(egress.SPEEDY)
    client.get("https://api.crossref.org/works")
    client.get("https://export.arxiv.org/api/query")
    assert client._hosts["api.crossref.org"].slots._value == 3
    assert client._hosts["export.arxiv.org"].slots._value == 1


def test_a_429_waits_for_retry_after_and_tries_again():
    client, transport, clock = make(egress.SPEEDY, FakeResponse(429, {"Retry-After": "7"}), FakeResponse(200))
    assert client.get("https://api.openalex.org/works").status_code == 200
    assert len(transport.calls) == 2 and 7.0 in clock.sleeps


def test_backoff_doubles_from_thirty_seconds_and_five_failures_stop_the_host():
    client, transport, clock = make(egress.POLITE, *[FakeResponse(503)] * 5)
    with pytest.raises(egress.HostStopped, match="5 failures in a row, last HTTP 503"):
        client.get("https://api.crossref.org/works")

    assert len(transport.calls) == 5
    assert [s for s in clock.sleeps if s >= 30] == [30.0, 60.0, 120.0, 240.0]
    with pytest.raises(egress.HostStopped):
        client.get("https://api.crossref.org/works")
    assert len(transport.calls) == 5
    # another host is unaffected
    assert client.get("https://doi.org/10.1/x").status_code == 200
    assert "5 failures" in client.summary()["api.crossref.org"]["stopped"]


def test_backoff_is_capped_at_an_hour(monkeypatch):
    monkeypatch.setattr(egress, "FAILURES_TO_STOP_HOST", 12)
    client, _, clock = make(egress.POLITE, *[FakeResponse(500)] * 11)
    client.get("https://api.crossref.org/works")
    assert max(clock.sleeps) == 3600.0


def test_a_success_resets_the_failure_count():
    responses = [FakeResponse(503)] * 4 + [FakeResponse(200)] + [FakeResponse(503)] * 4 + [FakeResponse(200)]
    client, transport, _ = make(egress.POLITE, *responses)
    client.get("https://api.crossref.org/works")
    client.get("https://api.crossref.org/works")
    assert len(transport.calls) == 10


def test_a_connection_failure_is_retried_like_a_5xx():
    client, transport, _ = make(egress.POLITE, requests.ConnectionError("down"), FakeResponse(200))
    assert client.get("https://api.crossref.org/works").status_code == 200
    assert len(transport.calls) == 2


def test_a_404_is_an_answer_not_a_failure():
    client, transport, clock = make(egress.POLITE, FakeResponse(404))
    assert client.get("https://doi.org/10.1/none").status_code == 404
    assert len(transport.calls) == 1 and clock.sleeps == []


def test_without_retry_a_failure_comes_back_at_once():
    client, transport, clock = make(egress.SPEEDY, FakeResponse(503), requests.Timeout("slow"))
    assert client.get("https://api.crossref.org/works", retry=False).status_code == 503
    with pytest.raises(egress.EgressFailed, match="Timeout"):
        client.get("https://api.crossref.org/works", retry=False)
    assert len(transport.calls) == 2 and max(clock.sleeps, default=0) < 1


def test_a_low_openalex_budget_stops_the_host():
    headers = {"X-RateLimit-Limit": "1.0", "X-RateLimit-Remaining": "0.015"}
    client, transport, _ = make(egress.SPEEDY, FakeResponse(200, headers))
    assert client.get("https://api.openalex.org/works").status_code == 200
    with pytest.raises(egress.HostStopped, match="budget low: 0.015 of 1 left today"):
        client.get("https://api.openalex.org/works")
    assert len(transport.calls) == 1
    assert client.summary()["api.openalex.org"]["budget_remaining"] == 0.015


def test_a_healthy_budget_is_recorded_and_does_not_stop():
    headers = {"X-RateLimit-Limit": "1.0", "X-RateLimit-Remaining": "0.9"}
    client, _, _ = make(egress.SPEEDY, FakeResponse(200, headers))
    client.get("https://api.openalex.org/works")
    client.get("https://api.openalex.org/works")
    assert client.summary()["api.openalex.org"] == {
        "requests": 2, "stopped": "", "budget_limit": 1.0, "budget_remaining": 0.9}


def test_stop_ends_a_wait():
    stop = threading.Event()
    client = Egress(egress.POLITE, contact_email="", transport=Transport(), stop=stop)
    client.get("https://api.crossref.org/works")
    stop.set()
    with pytest.raises(egress.Stopped):
        client.get("https://api.crossref.org/works")


def test_the_real_transport_never_follows_a_redirect(monkeypatch):
    seen = {}
    monkeypatch.setattr(requests, "request", lambda method, url, **kwargs: seen.update(kwargs) or FakeResponse(302))
    client = Egress(egress.SPEEDY, contact_email="")
    assert client.request("HEAD", "https://doi.org/10.1/x").status_code == 302
    assert seen["allow_redirects"] is False and seen["timeout"] == egress.DEFAULT_TIMEOUT


def test_the_default_mode_follows_the_size_of_the_run():
    assert egress.choose_mode(500) == egress.SPEEDY and egress.choose_mode(501) == egress.POLITE


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        Egress("frantic")


@pytest.fixture()
def crossref(monkeypatch):
    """The crossref module with its lookups sent to a fake transport."""
    from archivum import crossref as module

    transport = Transport()
    clock = FakeClock()
    client = Egress(egress.SPEEDY, contact_email="me@example.org", transport=transport, clock=clock, sleep=clock.sleep)
    monkeypatch.setattr(egress, "default", lambda: client)
    for f in (module.lookup_doi, module.search_by_title, module.search):
        f.cache_clear()
    return module, transport


def test_crossref_lookups_go_through_egress_with_one_attempt(crossref):
    module, transport = crossref
    transport.responses = [
        FakeResponse(200, payload={"message": {"DOI": "10.1/x"}}),
        FakeResponse(200, payload={"message": {"items": [{"title": ["T"]}]}}),
        FakeResponse(200, payload={"message": {"items": [{"title": ["B"]}]}}),
    ]
    assert module.lookup_doi("10.1/x") == {"DOI": "10.1/x"}
    assert module.search_by_title("T") == {"title": ["T"]}
    assert module.search(title="B", book_mode=True) == [{"title": ["B"]}]

    doi, title, generic = transport.calls
    assert doi["url"] == "https://api.crossref.org/works/10.1/x" and doi["timeout"] == 10
    assert title["params"] == {"query.title": "T", "rows": 1, "mailto": "me@example.org"}
    assert generic["params"]["filter"] == ("type:book",)


def test_crossref_lookups_return_nothing_on_failure(crossref):
    module, transport = crossref
    transport.responses = [FakeResponse(404), requests.ConnectionError("down"), FakeResponse(503)]
    assert module.lookup_doi("10.1/none") is None
    assert module.search_by_title("T") is None
    assert module.search(title="B") == []
    assert len(transport.calls) == 3
