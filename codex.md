# Codex Startup Context

This file is for future Codex sessions in this repository. Read it at the start of a session, along with `GEMINI.md`, before making code changes.

## Startup Checks

Always confirm the shell is actually in the repository before running repo-sensitive commands. A previous Codex tool call started in `C:\` even though the intended working directory was `C:\temp\GitHub\archivum_project`.

Use PowerShell:

```powershell
Get-Location
git rev-parse --show-toplevel
git status --short
```

Expected repository root:

```text
C:/temp/GitHub/archivum_project
```

If `Get-Location` reports `C:\`, stop and correct the working directory before running Git, tests, or file edits. Prefer passing the explicit workdir `C:\temp\GitHub\archivum_project` to tool calls.

## User Preferences

- The user is an expert Python programmer with a PhD in Mathematics and actuarial background.
- Use `uv` for Python package installation and environment management.
- Use `pathlib.Path` for Python file operations.
- Provide Windows PowerShell 7 (`pwsh`) commands and scripts unless the user explicitly asks otherwise.
- Use SI units for calculations.
- Use ISO 8601 dates (`YYYY-MM-DD`).
- Be concise, direct, and careful. This is a production project with real document-library data.

## Project Overview

Archivum is a personal document and reference management system, similar in purpose to Zotero or Mendeley.

Core concepts:

- Libraries are self-contained directories under local app data.
- Global config lives in `%LOCALAPPDATA%\archivum\global-config.yaml`.
- Metadata is stored as Pandas DataFrames in Feather files:
  - `ref.feather`
  - `doc.feather`
  - `ref-doc.feather`
- BibTeX is synchronized through `bibtex.bib`.
- The central data access class is `Library` in `src/archivum/library.py`.

Main modules:

- `src/archivum/library.py`: core data management and persistence.
- `src/archivum/cli.py`: Click CLI and interactive shell.
- `src/archivum/web/`: Flask web app, templates, static assets, routes.
- `src/archivum/document.py`: PDF processing and metadata discovery.
- `src/archivum/reference.py`: bibliographic reference model.
- `src/archivum/config.py`: Pydantic configuration.
- `src/archivum/import_bibtex.py`: BibTeX import logic.
- `src/archivum/bibtex.py`: internal/BibTeX conversion.
- `src/archivum/utilities.py`: shared helper functions.

## Web Interface Context

The web interface is launched through `archivum serve` and uses Flask, Bootstrap 5, HTMX-style updates, and streaming search.

Important current features:

- Streaming ripgrep full-text search.
- Standalone tag editor.
- Search history for query inputs.
- Query shortcuts for recent and random entries.
- CSV export.
- Status and health views.
- Report Studio with `.qmd` source files, Pandoc HTML rendering, Quarto PDF rendering, and artifact caching.
- Split-horizon access control based on client IP, with admin and read-only modes.

When adding or changing web interface or core user-facing behavior, update:

```text
src/archivum/web/templates/help.html
```

## Safety Rules

- Treat the library metadata and document store as production data.
- Do not delete source documents unless the user explicitly asks and confirms the exact operation.
- Be especially careful around hardlinks, sharded document storage, Feather files, and BibTeX synchronization.
- Assume multiple libraries can share the same physical document store.
- Do not revert unrelated changes in the Git worktree.
- Current known untracked file at the time this file was created:

```text
?? temp/qmd_extract.qmd
```

## Useful First Commands

Run these when orienting:

```powershell
Get-Location
git rev-parse --show-toplevel
git status --short
Get-ChildItem -Force | Select-Object -First 20 Name,Mode
Get-Content -LiteralPath 'GEMINI.md' -TotalCount 120
```

For searching:

```powershell
rg --files
rg "search text" src docs
```

Use `rg` first when searching files or text.

## Development Notes

- Prefer existing project patterns over new abstractions.
- Keep edits tightly scoped.
- Add tests in proportion to risk and blast radius.
- Use structured parsers/APIs where available instead of ad hoc text manipulation.
- For frontend work, preserve the existing Bootstrap/HTMX style and high-density operational UI.
- Avoid touching real library data unless the task explicitly requires it.

## Test Automation Handoff - 2026-05-13

User asked for more automated web testing, then paused that work to fix underlying issues first.

Implemented so far:

- Added pytest configuration and test optional dependency metadata in `pyproject.toml`.
- Tightened `requires-python` from `>=3.10` to `>=3.13` because `uber-shell==1.0.0` requires Python 3.13 and `uv run --extra test` could not resolve otherwise.
- Added missing runtime dependency `lark` to `pyproject.toml`; `cli.py` imports it.
- Added a compatibility fallback in `src/archivum/cli.py` for environments where `rustfuzz` does not export `FuzzyMatcherMultiHi`.
- Added `tests/conftest.py` with active-library fixtures, Flask test client setup, route response helpers, logging helpers, and live-server support.
- Added `tests/web/test_routes.py` covering:
  - query page recent/read/hash-prefix routes,
  - ripgrep summary/counts/details routes for `risk measure`,
  - authors page and author selection,
  - slow social/semantic network route tests.
- Added `tests/web/test_browser_smoke.py` with focused Playwright smoke tests for query, ripgrep, authors, and network data handoff.
- Added `scripts/Test-ArchivumWeb.ps1` with `Changed`, `Fast`, `Slow`, and `All` modes. It now uses the active environment via `python -m pytest` rather than `uv run`, because `querexfuzz` is currently available from the user's ambient Python path, not from project metadata.

Validation done:

```powershell
python -m pytest -m "web and not slow and not browser"
```

Passed: 3 tests.

Important observations:

- The active library loaded as `uber-library` / `Uber Library` with about 7081 refs, 7427 docs, and 6917 ref-doc links.
- `risk measure` ripgrep route tests are heavy but pass; summary/details can produce nearly 1 MB responses.
- `pytest` and `pytest-playwright` were installed into the active Conda environment using:

```powershell
uv pip install pytest pytest-playwright
```

Underlying issues to fix before resuming testing:

- `querexfuzz` is a private/local dependency from `C:\Users\steve\Documents\SynologyDrive\TELOS\Python\querexfuzz_project\src\querexfuzz\__init__.py`. The project metadata does not declare how to install it, so clean `uv run` environments cannot run Archivum.
- The `rustfuzz` package installed by uv did not expose `FuzzyMatcherMultiHi`; decide whether the fallback in `cli.py` is acceptable or whether the dependency/version/source should be fixed.
- Slow network tests initially failed because social payloads do not expose a top-level `edges` key; edge-like data appears inside `elements`. The test was adjusted to derive edge count from `elements`.
- Slow `rg risk measure` network/semantic tests were too expensive on the full active library. Test fixtures were adjusted to:
  - `q top 50 title ~ /risk measure/`
  - `rg risk measure -g 0*.md`
- A rerun of the slow route tests was started after those changes but was interrupted by the user before completion.

Suggested next steps:

1. Fix dependency/source-of-truth issues for `querexfuzz`, `rustfuzz`, and Python version metadata.
2. Re-run:

```powershell
python -m pytest -m "web and not slow and not browser"
python -m pytest -m "web and slow and not browser" --run-slow-web
```

3. Once route tests are stable, install Playwright browser binaries if needed and run:

```powershell
python -m playwright install chromium
python -m pytest -m "web and browser" --run-browser --run-slow-web
```

4. Revisit whether slow semantic tests should write to the production `semantic-embeddings.feather` cache or use an isolated copied library.

## Dependency/Test Update - 2026-05-13

Follow-up dependency work was implemented after the handoff above:

- `pyproject.toml` now uses public Git dependencies for:
  - `querexfuzz @ git+https://github.com/mynl/querexfuzz_project.git`
  - `rustfuzz @ git+https://github.com/mynl/rustfuzz.git`
- `uber-shell>=1.1.0` is used from PyPI.
- The temporary `rustfuzz` fallback in `src/archivum/cli.py` was removed; the Git `rustfuzz` package provides `FuzzyMatcherMultiHi`.
- Clean `uv` installs exposed missing runtime dependencies, now added explicitly:
  - `tzlocal`
  - `watchdog`
  - `ipython`
  - `pybtex`
  - `networkx`
- `scripts/Test-ArchivumWeb.ps1` now defaults back to `uv run --extra test pytest`.
- The runner sets `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` so `rustfuzz` builds with Python 3.13 and PyO3 0.20.
- Test search phrase changed from `risk measure` to `spectral risk measure`.
- The temporary ripgrep `-g 0*.md` restriction was removed.

Validated commands:

```powershell
uv lock
$env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY = "1"
uv run --extra test python -c "from rustfuzz import FuzzyMatcherMultiHi; from querexfuzz.core import Querexfuzz; import uber_shell; print('ok')"
.\scripts\Test-ArchivumWeb.ps1 -Mode Fast
.\scripts\Test-ArchivumWeb.ps1 -Mode Slow
```

Results:

- Fast suite: 3 passed.
- Slow route suite: 5 passed, 4 skipped, 9 deselected at this point.
- The skipped tests were semantic tests because optional semantic packages (`hdbscan`, `umap`, `sentence_transformers`) were not installed in the uv environment.

Remaining cleanup:

- The repo contains an inaccessible `temp/.pytest_cache` from earlier test runs; pytest now uses `temp/pytest-cache`, but Git still warns when scanning the old directory.

## Semantic Dependency Update - 2026-05-13

Added semantic/network analysis dependencies to `pyproject.toml` and `uv.lock`:

- `hdbscan`
- `umap-learn` (imported as `umap`)
- `sentence-transformers` (imported as `sentence_transformers`)

Validated imports:

```powershell
$env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY = "1"
uv run --extra test python -c "import hdbscan, umap, sentence_transformers; print('semantic deps ok')"
```

Then reran:

```powershell
.\scripts\Test-ArchivumWeb.ps1 -Mode Slow
```

Current result:

- Slow route suite: 9 passed, 9 deselected.
- Semantic tests now run rather than skip.
- First semantic title run took about 16 seconds; later semantic/text and rg semantic cases were fast because embeddings/model/cache were warm.

Warnings still seen:

- `greater_tables` emits a Pandas future warning.
- `umap` warns that `n_jobs` is overridden when `random_state` is set.
- Git still warns about old inaccessible pytest cache directories.

## Semantic Model Cache Update - 2026-05-13

The semantic network path now uses an explicit Archivum app-level model cache:

- Model: `all-MiniLM-L6-v2`
- Cache root: `BASE_DIR / "models" / "sentence-transformers"`; on Steve's Windows setup this is under `%LOCALAPPDATA%\archivum\models\sentence-transformers`.
- `SentenceTransformer` is loaded with `cache_folder=<that cache>`.
- If cached model weights exist there, the loader tries `local_files_only=True` first to avoid Hub checks.
- If the local cached load fails, it logs a warning and falls back to Hub access.
- The model object remains cached in-process via `_MODEL_CACHE["transformer"]`.
- Encoding now passes `show_progress_bar=False`.
- Verbose semantic payloads include `Model:` and `Model cache:` log messages.

Validated commands:

```powershell
uv run --extra test pytest tests/test_semantic_model_cache.py -q
.\scripts\Test-ArchivumWeb.ps1 -Mode Fast
.\scripts\Test-ArchivumWeb.ps1 -Mode Slow
uv run --extra test pytest -q
```

Results:

- Semantic model cache unit tests: 3 passed.
- Fast web suite: 3 passed.
- Slow web/network suite: 9 passed.
- Default pytest selection: 6 passed, 15 skipped.

Note: the slow semantic route run did not need to load/download the transformer because the library's `semantic-embeddings.feather` already had the needed paper embeddings. The first semantic route still took about a minute, likely from cold UMAP/Numba setup.
