from __future__ import annotations

import logging

import pytest

from conftest import logged_step, require_module


logger = logging.getLogger("archivum.tests.web.browser")


def _require_browser_global(page, name: str) -> None:
    if not page.evaluate(f"() => typeof window.{name} !== 'undefined'"):
        pytest.skip(f"browser dependency {name!r} did not load")


@pytest.mark.web
@pytest.mark.browser
@pytest.mark.active_library
def test_query_htmx_recent_smoke(browser_page, live_server):
    page = browser_page
    with logged_step("browser-query-recent"):
        page.goto(f"{live_server}/", wait_until="domcontentloaded")
        _require_browser_global(page, "htmx")
        page.locator("#search-input").wait_for()
        page.evaluate("() => executeRecent(50)")
        page.locator("#results .list-item, #results table, #results .muted").first.wait_for()
        text = page.locator("#results").inner_text(timeout=120_000)
        logger.info("browser query results text chars=%s", len(text))
        assert "No library open" not in text
        assert "Query error" not in text


@pytest.mark.web
@pytest.mark.browser
@pytest.mark.active_library
def test_ripgrep_htmx_summary_and_details_smoke(browser_page, live_server, risk_measure_query):
    page = browser_page
    with logged_step("browser-ripgrep-summary-details", query=risk_measure_query):
        page.goto(f"{live_server}/ripgrep", wait_until="domcontentloaded")
        _require_browser_global(page, "htmx")
        page.locator("#rg-input").fill(risk_measure_query)

        page.get_by_role("button", name="Summary").click()
        page.locator("#rg-results .rg-summary-dashboard, #rg-results .muted").first.wait_for(timeout=120_000)
        summary_text = page.locator("#rg-results").inner_text(timeout=120_000)
        logger.info("browser ripgrep summary chars=%s", len(summary_text))
        assert "No library open" not in summary_text

        page.get_by_role("button", name="Details").click()
        page.locator("#rg-results .rg-block, #rg-results .muted").first.wait_for(timeout=120_000)
        details_text = page.locator("#rg-results").inner_text(timeout=120_000)
        logger.info("browser ripgrep details chars=%s", len(details_text))
        assert "No library open" not in details_text


@pytest.mark.web
@pytest.mark.browser
@pytest.mark.active_library
def test_authors_browser_select_author_smoke(browser_page, live_server):
    page = browser_page
    with logged_step("browser-authors-select"):
        page.goto(f"{live_server}/authors", wait_until="domcontentloaded")
        _require_browser_global(page, "htmx")
        page.locator(".author-item").first.wait_for(timeout=120_000)
        author = page.locator(".author-item").first.inner_text()
        page.locator(".author-item").first.click()
        page.locator("#author-results .list-item, #author-results table, #author-results .muted").first.wait_for(timeout=120_000)
        text = page.locator("#author-results").inner_text(timeout=120_000)
        logger.info("browser author=%r result chars=%s", author, len(text))
        assert "No library open" not in text
        assert "Author search error" not in text


@pytest.mark.web
@pytest.mark.browser
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.active_library
@pytest.mark.parametrize(
    ("query", "mode", "source"),
    [
        ("q top 50 title ~ /spectral risk measure/", "social", "title"),
        ("rg spectral risk measure", "social", "title"),
        ("q top 50 title ~ /spectral risk measure/", "semantic", "title"),
        ("q top 50 title ~ /spectral risk measure/", "semantic", "text"),
        ("rg spectral risk measure", "semantic", "title"),
        ("rg spectral risk measure", "semantic", "text"),
    ],
)
def test_network_browser_data_reaches_graph_smoke(browser_page, live_server, query, mode, source):
    if mode == "semantic":
        require_module("hdbscan")
        require_module("umap")
        require_module("sentence_transformers")

    page = browser_page
    with logged_step("browser-network", query=query, mode=mode, source=source):
        page.goto(f"{live_server}/network", wait_until="domcontentloaded")
        _require_browser_global(page, "cytoscape")
        page.locator("#network-search").fill(query)
        if mode == "semantic":
            page.evaluate("(source) => setSemanticSource(source)", source)
            page.locator("#semantic-btn").click()
        else:
            page.locator("#social-btn").click()

        page.wait_for_function(
            "() => window.currentData && typeof window.currentData.papers !== 'undefined'",
            timeout=180_000,
        )
        payload = page.evaluate(
            """() => ({
                papers: window.currentData.papers,
                elements: (window.currentData.elements || []).length,
                nodes: (window.currentData.nodes || []).length,
                edges: (window.currentData.edges || []).length,
                clusters: (window.currentData.clusters || []).length,
                mode: window.currentData.mode
            })"""
        )
        logger.info("browser network payload=%s", payload)
        assert payload["mode"] == mode
        assert "No matches found" not in page.locator("#network-stats").inner_text(timeout=120_000)
