# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## User Profile And Conventions

- The user is an expert Python developer (PhD Math, qualified actuary). Skip basic explanations.
- Always use `uv` for environment and dependency management.
- Always use `pathlib.Path` for filesystem operations.
- **PowerShell 7 (`pwsh`) only — never Bash idioms.** No `awk`, `sed`, `grep` pipelines, `$(...)` substitution, heredocs, `2>/dev/null`, etc. PowerShell equivalents: `Select-Object`, `Where-Object`, `ForEach-Object`, `$env:VAR`, `2>$null`. If you find yourself reaching for a bash one-liner, stop and write the PowerShell version.
- **For file content search, use `rg` (ripgrep)** — it's a required project dependency. Avoid `Select-String`, `findstr`, or `grep`. The `Grep` tool (which wraps `rg`) is preferred over shelling out, but if you must shell out, it's `rg "pattern" path`, not anything else.
- ISO 8601 dates (`YYYY-MM-DD`) and SI units.
- This is a **production project with real document-library data.** Treat libraries, BibTeX, and the sharded document store accordingly.

## Common Commands

Environment setup:

```powershell
uv sync --extra test
uv lock                       # after dependency changes
```

CLI sanity check:

```powershell
uv run archivum --help
uv run archivum uber -a                       # open default library in REPL
uv run archivum serve -b                      # web app on http://127.0.0.1:9124
uv run archivum serve <lib> --prod --address 0.0.0.0
```

Tests — use the project runner; it picks markers, slow/browser flags, and pytest extras correctly:

```powershell
.\scripts\Test-ArchivumWeb.ps1 -Mode Fast    # web and not slow and not browser
.\scripts\Test-ArchivumWeb.ps1 -Mode Slow    # web (incl. slow) but not browser
.\scripts\Test-ArchivumWeb.ps1 -Mode All     # web + slow + browser
.\scripts\Test-ArchivumWeb.ps1 -Mode Changed # auto-decide based on git diff
```

Direct pytest (when you need it):

```powershell
uv run --extra test pytest -q
uv run --extra test pytest tests/test_report_generation.py -q
uv run --extra test pytest -m "web and slow and not browser" --run-slow-web
uv run --extra test pytest -m "web and browser" --run-browser --run-slow-web
uv run --extra test python -m playwright install chromium   # one-time for browser tests
```

Test runner notes:

- `Test-ArchivumWeb.ps1` defaults to `uv run --extra test pytest`. Pass `-Python <path>` to use a different interpreter, `-PytestArgs "..."` to forward args.
- Slow tests must be explicitly enabled with `--run-slow-web`; browser tests with `--run-browser`. The pytest plugin in `tests/conftest.py` skips them otherwise.
- Many tests require an active library — they call `Library(DEFAULT_LIBRARY)` from the global config and skip if none is configured (`ARCHIVUM_LIBRARY` env var overrides). The user's machine typically has `uber-library` loaded with ~7k refs/docs.
- Pytest cache is `temp/pytest-cache/` (not the default `.pytest_cache/`).

Version bumps:

- `pyproject.toml` `project.version` is the source of truth. Bump it (semver) after every code change. `archivum.__version__` is read at runtime via `importlib.metadata`.

Docs build is driven by `doc-test.bat <python-version> new|refresh` (Windows CMD; clones the repo into `C:\tmp\archivum_project_rtd_build_<ver>` and runs Sphinx). ReadTheDocs config: `.readthedocs.yaml`.

## Architecture

### Data model

Three Feather DataFrames per library; one BibTeX file kept in sync:

- `ref.feather` — bibliographic references, keyed by `tag` (e.g. `Wang2024`).
- `doc.feather` — physical files, keyed by content hash plus version.
- `ref-doc.feather` — many-to-many join between tags and document hashes.
- `bibtex.bib` — synchronized projection of `ref.feather`.

A single sharded document store (`doc_store_lib`) can back **multiple libraries**, so different libraries may have divergent metadata for the same hash. Be cautious about anything that touches canonical filenames or hardlinks. The definitive store is `\s\ShardedDocLibrary` on the user's machine; on the `Kolmogorov` host, `~\RawDocs\{Books,Book_scans,Library}` are hard-linked back to the originals in `S`.

### Configuration hierarchy

- Global: `%LOCALAPPDATA%\archivum\global-config.yaml` — `default_library`, `doc_store_lib`, `debug_dir`, theme, plus library defaults (ref_columns, enhancement_strategies, timezone, tablefmt, extractor, hash_workers, tag_name_mapper).
- Per-library: `%LOCALAPPDATA%\archivum\libraries\<name>\config.yaml` — `name`, `description`, `bibtex_file`, plus any global overrides. Validated by Pydantic `Configurator` in `src/archivum/config.py`.
- Constants and the app-data folder are bootstrapped in `src/archivum/__init__.py` (`BASE_DIR`, `LIBRARIES_DIR`, `GLOBAL_CONFIG`, `resolve_path()`).

### Module map

- `src/archivum/library.py` + `library_base.py` — `Library` is the central data access layer (loads/saves Feathers, syncs BibTeX, runs ripgrep, hosts import/audit). Anything touching library state goes through it. Has filesystem watchers (`watchdog`) and a `needs_reload` flag picked up by the web before-request hook.
- `src/archivum/cli.py` — Click CLI + `uber` interactive shell (built on `uber_shell` + `prompt_toolkit` + `skimmatch.FuzzyMatcherMultiHi`). `LibraryContext` singleton holds the active library for both CLI and web.
- `src/archivum/document.py` — Gather → Enhance → Verify metadata extraction for PDFs (pymupdf, OCR fallback for largest-font title detection, Crossref/Arxiv enrichment).
- `src/archivum/import_bibtex.py` — `Bib2df_Incremental`, the canonical importer used by both the `import-bibtex` CLI and the web Ingest flow. CLI behavior is the reference; the web should mirror it.
- `src/archivum/bibtex.py`, `reference.py`, `enhancements.py`, `crossref.py`, `arxiv.py`, `hasher.py`, `trie.py`, `tag_replacers.py`, `utilities.py` — supporting models, external lookups, hashing, tag allocation, name completion.
- `src/archivum/search/` — `query.py` normalization, `universe.py` shared resolver for querexfuzz + ripgrep hash universes (use `resolve_universe_details(...)` when you need the displayed `rg` command + cache-hit state).
- `src/archivum/analytics/` — `semantic.py` (sentence-transformers + UMAP + HDBSCAN), `networks.py` (NetworkX coauthor + semantic graphs with Matplotlib SVG output for reports), `timing.py` for verbose spans.
- `src/archivum/web/` — Flask app:
  - `app.py` is the factory; sets `g.is_admin` based on `request.remote_addr` (see Auth below) and kicks off semantic warmups in the background.
  - `routes/` — blueprint package. Each file is a topic: `query`, `ripgrep`, `ingest`, `edit`, `reports`, `network`, `qmd`, `documents`, `analytics`, `status`. All share state via `routes/shared.py` (currently `from .shared import *` — there's a known cleanup item to make these explicit).
  - `services/` — heavier business logic extracted out of routes (`ripgrep.py`, `network.py`, `exports.py`).
  - `presenters/search.py` — search result formatting for templates.
  - `cache.py`, `decorators.py` (`@admin_required` returns 403 for non-admin), `templates/`, `static/`.
- `src/archivum/configurations/` — packaged YAML for logging and querexfuzz DataFrame configs (shipped via `setuptools.package-data`).
- `src/archivum/quarto.py`, `rg_tools.py`, `definitive_loader.py`, `alex.py`, `gui.py` — Quarto/QMD parsing, ripgrep wrappers, full-store loader, OpenAlex helpers, Tkinter metadata editor.

### Querying

- `querexfuzz` extends DataFrames with `.querex(...)` for a regex/SQL/fuzzy DSL. Used everywhere — CLI, web query page, universe resolution. Plain text typed into the web query box becomes `# text`; querex-symbol input is wrapped as `recent top 50 ...`; explicit `q ...` passes through.
- `rustfuzz.FuzzyMatcherMultiHi` powers tag/title/hash autocompletion. Both `querexfuzz` and `rustfuzz` are Git-installed dependencies (see `pyproject.toml`); do not reintroduce import fallbacks for them.
- Full-text search uses external `ripgrep` (`rg`) over extracted text under `full_text_lib`. Results stream to the web UI via Flask generators + HTMX OOB swaps (`hx-swap-oob="beforeend:#rg-results"`). Network-page rg is case-insensitive by default; the Options menu's `Case sensitive rg` toggle sends `case=sensitive` and the resolver omits `-i`. Cache keys include case sensitivity.

### Semantic / Network

- Model is `all-MiniLM-L6-v2`, cached under `BASE_DIR / "models" / "sentence-transformers"`. Per-paper embeddings are cached in the library's `semantic-embeddings.feather`. The loader prefers `local_files_only=True` and falls back to the Hub on miss.
- Full-text semantic sources currently support title/metadata, first 2,000 characters, and first 4,000 characters — preserve these labels when changing routes/templates.
- UMAP is intentionally allowed to use multiple cores — do **not** add `random_state` (that forces serial behavior). Verbose timing logs in semantic/network payloads are user-visible; preserve spans for universe resolution, embedding cache load/lookup, model load, missing-embedding generation, UMAP, HDBSCAN, cluster summaries, serialization, and browser fetch/render.
- Network and Semantic pages share universe resolution (`search/universe.py`). Verbose responses should include the displayed ripgrep command and cache hit/miss when an `rg` clause is present.

### Web auth (split-horizon)

- In `src/archivum/web/app.py`: admin = anyone whose `request.remote_addr` is **not** `10.8.0.1` (the VPS bridge). Everything else (local, LAN `192.168.x.x`, VPN `10.8.0.2`) gets admin. `10.8.0.1` traffic is read-only and blocked from modification routes by `@admin_required`.
- This negative-IP rule is acknowledged as fragile; tightening it (configurable CIDRs, trusted proxy handling) is a known planned change. Don't rewrite it casually.

### Ingest flow (web)

- Admin Ingest page stages a file, lets the user edit BibTeX, previews via `Library.preview_staged_document_import(...)` (which runs the real `Bib2df_Incremental` with `write_audit=False`), then commits via `Library.import_staged_document(...)` (which reruns the importer with `write_audit=True`, saves through `update_library(save=True)`, shards the document, writes audit files, and extracts text for new PDFs).
- Previewed tags are **advisory**: `map_tags()` resets the allocator at commit time, so the committed tag can differ if the library changed between preview and commit.
- Do not reintroduce the old ad hoc `Normalize Names` / `Generate Tag` buttons — preview goes through the importer.

### Reports (Report Studio)

- `.qmd` source files in each library's `exports/` directory are the source of truth; artifacts are ephemeral but cached.
- Web view: `pandoc --citeproc` → naked HTML fragment, cached by source mtime.
- PDF: `quarto render` with the `tectonic` engine.
- Sidecar `<slug>.report.json` records the recipe so the Edit button can reload Report Studio. Old reports without sidecars get a greyed-out Edit button. Trash deletes `.qmd`, `.html`, `.pdf`, `.report.json`, and owned report SVGs.
- Network reports write SVGs with Matplotlib: semantic produces a cluster-hull overview plus a galaxy map with sampled tag labels; social produces a coauthor graph with wrapped black labels. PDF embeds use local filenames; HTML rewrites them to `/reports/asset/<asset-name>`.

## Repo Layout Conventions

- `docs/` is **Sphinx documentation only** (`api.rst`, `quickstart.rst`, etc.). Built by `doc-test.bat`, published to ReadTheDocs. Do not put planning, design, or migration docs here.
- `dev/projects/` is for **migration and refactor planning briefs** — cold-start instruction docs for multi-session work like the SQLite migration. Each project gets its own self-contained markdown file (goal, prereqs, files to read first, plan, acceptance tests, rollback, DoD checklist).
- `human-notes.md`, `codex.md`, `GEMINI.md` at the repo root carry the user's running notes and prior AI-session handoffs — read for archaeology when the question is "why is X the way it is."

## Things To Know Before You Edit

- **Check `TODO.md` and remind the user.** Read it at the start of any change session. If an open item touches the area being worked on, say so up front and ask whether to fold it in — the user wants to be prompted, not to have to remember. Move finished items to the Done section with the version they shipped in.
- **Help template is a hard rule:** when you change web-visible behavior, update `src/archivum/web/templates/help.html`.
- **Non-destructive default:** never delete source documents without explicit confirmation. Sharding uses hardlinks. Many CLI commands require `-x` / `--execute` to actually write.
- **Multiple libraries, one store:** different libraries may have divergent metadata for the same hash. Operations that rewrite canonical filenames can cause libraries to "fight" over a file.
- **No reverting unrelated worktree changes.** The user often has in-progress edits checked out.
- **Library lifecycle in tests:** `tests/conftest.py` provides session-scoped `active_library` and per-test `app`/`client`/`live_server` fixtures. Slow semantic tests currently can write to the production `semantic-embeddings.feather` — be aware before adding new ones.
- **Avoid backwards-compat hacks.** Prefer fixing the root cause to leaving import shims, compatibility flags, or env-var workarounds.
- **CSS/UI density:** Bootstrap 5 + HTMX-style OOB swaps. Sidebar pages use `height: calc(100vh - 120px)` with independent scroll containers; reflow stacked on `max-width: 767.98px`. Preserve the high-density operational layout.

## Companion Files In The Repo Root

- `TODO.md` — small deferred items, each scoped to the area that will trigger it. **Read this every session and surface relevant items to the user** (see Things To Know above). Larger multi-session work goes in `dev/projects/` instead.
- `CHANGELOG.md` — notable shipped changes, keyed to `project.version`. Add an entry whenever you bump the version. Starts at 2.3.0; earlier history is in the `Version-*` git tags.
- `human-notes.md` — the user's running roadmap, ideas, and prioritized improvement items (the "Improvement Roadmap" section enumerates known performance/security/maintainability targets). Treat as current intent when prioritizing.
- `codex.md` — chronological session handoffs (test setup history, dependency changes, semantic/report work). Useful for "why is X the way it is" archaeology.
- `GEMINI.md` — the older architectural overview and Web Interface Overhaul history.
