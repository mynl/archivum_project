# Project 10 — Citations from OpenAlex  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 07 (or 08).

## One-line goal

Populate `citation(tag PK, count, source, fetched_at)` lazily from OpenAlex. Trigger fetch on search-result interaction; apply a "high-quality" filter so we don't burn rate-limit on low-signal entries. Manual override supported.

## Decisions already locked

- Table: `citation(tag PK, count INTEGER, source TEXT, fetched_at TIMESTAMP)`. One row per tag, upserted on refresh.
- Source: OpenAlex (free, generous rate limit, polite pool with email).
- Pull on demand from search-result interaction, NOT bulk-precomputed.
- "High-quality" filter for triggering: DOI present + journal/booktitle present + year ≥ 2000 (refine with user).
- Manual override: separate row with `source='manual'` wins over auto-fetched.

## Files to read first

- `src/archivum/crossref.py`, `src/archivum/arxiv.py` — existing external-API client patterns.
- `src/archivum/enhancements.py` — where Crossref lookups currently happen.
- `src/archivum/web/routes/query.py` — where search results render (natural trigger point for lazy fetch).
- OpenAlex API docs (live).

## Open design questions (resolve when writing the brief)

- Rate limiting: OpenAlex allows ~10 req/s anonymous, 100 req/s polite (with email). Use polite. Per-request throttle with a small `time.sleep`, plus a session-level circuit breaker.
- UI surface: probably an extra column on query results plus a row on the tag detail view. Refresh button + "auto-refresh if older than 90 days when the row is touched."
- Refresh failure handling: store `last_fetched_attempt` to avoid re-trying recently-failed lookups.
- Should we also store a small history of count over time (e.g. monthly snapshot rows)? Probably not in this project — keep `citation` as current-state. Could add `citation_log` later if needed.

## Risks / gotchas

- DOI lookup vs title lookup: prefer DOI when available; fall back to title only with high-confidence match (OpenAlex score threshold).
- Long-tail "no result" responses: store sentinel `count=NULL, source='openalex-notfound', fetched_at=...` so we don't retry on every render.
- Don't let citation fetches block the search results render. Background queue + render-without-counts-first, fill in via HTMX swap.

## Likely drift sources (revisit this stub after)

- "High-quality" filter definition will likely change after we see real hit rates against the user's actual library.
