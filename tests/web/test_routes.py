from __future__ import annotations

import logging
from urllib.parse import quote

import pytest

from conftest import assert_json_ok, assert_ok_html, logged_step, log_payload_counts, require_module


logger = logging.getLogger("archivum.tests.web.routes")


@pytest.mark.web
@pytest.mark.route
@pytest.mark.active_library
def test_query_screen_recent_read_and_hash_prefix(client, sample_hash_prefix):
    scenarios = [
        ("recent", "q top 50 recent", "results found"),
        (
            "read-history",
            "q top 50 select last_read, read_count, * where read_count > 0 order by -last_read",
            None,
        ),
        (
            "hash-prefix",
            f"q top 25 select year, * hash ~ ^{sample_hash_prefix} order by -year",
            "results found",
        ),
    ]

    with logged_step("query-page"):
        page = assert_ok_html(client.get("/"), context="query page")
        assert 'id="search-input"' in page

    for name, query, expected in scenarios:
        with logged_step("query-search", scenario=name, query=query):
            response = client.get(
                "/search",
                query_string={"q": query},
                headers={"HX-Request": "true"},
            )
            html = assert_ok_html(response, context=f"query {name}")
            assert "Query error" not in html
            if expected is not None:
                assert expected in html
            logger.info("query scenario=%s bytes=%s", name, len(html))


@pytest.mark.web
@pytest.mark.route
@pytest.mark.active_library
def test_ripgrep_summary_counts_and_details(client, risk_measure_query):
    with logged_step("ripgrep-page"):
        page = assert_ok_html(client.get("/ripgrep"), context="ripgrep page")
        assert 'id="rg-input"' in page

    scenarios = [
        ("summary", {"q": risk_measure_query, "summary": "true"}, "Chronological Summary"),
        ("counts", {"q": risk_measure_query, "counts": "true"}, "documents matched"),
        ("details", {"q": risk_measure_query}, "rg-block"),
    ]

    saw_match = False
    for name, query_string, expected in scenarios:
        with logged_step("ripgrep-search", scenario=name, query=risk_measure_query):
            response = client.get(
                "/rg-search",
                query_string=query_string,
                headers={"HX-Request": "true"},
            )
            html = assert_ok_html(response, context=f"ripgrep {name}")
            assert "No library open" not in html
            if "No matches found" in html or "No full-text matches found" in html:
                logger.warning("ripgrep scenario=%s produced no matches for %r", name, risk_measure_query)
                continue
            saw_match = True
            assert expected in html
            logger.info("ripgrep scenario=%s bytes=%s", name, len(html))

    if not saw_match:
        pytest.skip(f"No ripgrep matches found for {risk_measure_query!r} in active library.")


@pytest.mark.web
@pytest.mark.route
@pytest.mark.active_library
def test_authors_screen_selects_author(client, sample_author):
    with logged_step("authors-page"):
        page = assert_ok_html(client.get("/authors"), context="authors page")
        assert 'id="author-list"' in page
        assert "author-item" in page

    with logged_step("author-search", author=sample_author):
        response = client.get(
            f"/author-search/{quote(sample_author, safe='')}",
            headers={"HX-Request": "true"},
        )
        html = assert_ok_html(response, context="author search")
        assert "Author search error" not in html
        assert "results found" in html or "No results found" in html
        logger.info("author-search author=%s bytes=%s", sample_author, len(html))


@pytest.mark.web
@pytest.mark.route
@pytest.mark.active_library
def test_report_entrypoints_render(client):
    with logged_step("query-report-button"):
        query_page = assert_ok_html(client.get("/"), context="query report button")
        assert 'id="query-report-btn"' in query_page
        assert "openReportStudio()" in query_page

    with logged_step("network-report-button"):
        response = client.get("/network")
        assert response.status_code == 200
        network_page = response.get_data(as_text=True)
        assert "traceback" not in network_page.lower()
        assert 'id="net-report-btn"' in network_page
        assert "openNetworkReport()" in network_page

    with logged_step("report-studio-source-fields"):
        reports_page = assert_ok_html(
            client.get("/reports", query_string={"source": "semantic", "q": "q top 5 recent", "semantic_source": "title", "abstract": "0"}),
            context="report studio source fields",
        )
        assert 'name="source" value="semantic"' in reports_page
        assert 'name="include_abstract"' in reports_page
        assert 'id="abstract-off" value="0"' in reports_page


@pytest.mark.web
@pytest.mark.route
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.active_library
@pytest.mark.parametrize(
    "query_fixture",
    ["risk_measure_q_query", "risk_measure_rg_query"],
)
def test_network_social_payloads(client, request, query_fixture):
    query = request.getfixturevalue(query_fixture)
    with logged_step("network-social", query=query):
        response = client.get(
            "/network-data",
            query_string={"q": query, "verbosity": "verbose"},
            headers={"HX-Request": "true"},
        )
        payload = assert_json_ok(response, context=f"social {query}")
        log_payload_counts("social", payload)
        assert "papers" in payload
        assert "elements" in payload
        assert "nodes" in payload
        assert any(
            message.startswith("Timing: ")
            for message in payload.get("log_messages", [])
        )
        edge_count = len([el for el in payload.get("elements", []) if el.get("data", {}).get("source")])
        logger.info("social derived edge_count=%s", edge_count)

        if int(payload.get("papers") or 0) == 0:
            pytest.skip(f"No social network papers matched {query!r}.")


@pytest.mark.web
@pytest.mark.route
@pytest.mark.network
@pytest.mark.semantic
@pytest.mark.slow
@pytest.mark.active_library
@pytest.mark.parametrize(
    ("query_fixture", "source"),
    [
        ("risk_measure_q_query", "title"),
        ("risk_measure_q_query", "text"),
        ("risk_measure_rg_query", "title"),
        ("risk_measure_rg_query", "text"),
    ],
)
def test_network_semantic_payloads(client, request, query_fixture, source):
    require_module("hdbscan")
    require_module("umap")
    require_module("sentence_transformers")

    query = request.getfixturevalue(query_fixture)
    with logged_step("network-semantic", query=query, source=source):
        response = client.get(
            "/semantic-data",
            query_string={"q": query, "source": source, "verbosity": "verbose"},
            headers={"HX-Request": "true"},
        )
        payload = assert_json_ok(response, context=f"semantic {query} {source}")
        log_payload_counts("semantic", payload)
        assert "papers" in payload
        assert "elements" in payload
        assert "clusters" in payload
        assert "omitted_count" in payload or payload.get("papers") == 0
        assert "status_msg" in payload or payload.get("papers") == 0
        if payload.get("log_messages"):
            messages = payload["log_messages"]
            assert any(message.startswith("Model: ") for message in messages)
            assert any(message.startswith("Model cache: ") for message in messages)
            assert any(message.startswith("Timing: ") for message in messages)

        if int(payload.get("papers") or 0) == 0:
            pytest.skip(f"No semantic papers matched {query!r} source={source!r}.")
