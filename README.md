# Archivum

Archivum (Latin for archive) is a personal document and reference management system for academic papers,
books, reports, and technical documents. It is built around content-addressable
document identity: files are tracked by hash, bibliographic entries are tracked by
tag, and the relationship between them is stored explicitly.

The project is optimized for a local, production document library rather than a
public multi-user service. Treat the configured libraries and document store as
real data.

## What It Does

- Manages self-contained libraries under local app data.
- Stores metadata in Feather files for fast Pandas access:
  - `ref.feather`: bibliographic reference metadata.
  - `doc.feather`: document file metadata, hashes, versions, and paths.
  - `ref-doc.feather`: links between reference tags and document hashes.
- Synchronizes reference metadata with BibTeX.
- Organizes documents in a sharded content-addressable document store.
- Provides a Click CLI, an Uber Shell interactive interface, and a Flask web UI.
- Supports full-text search through ripgrep over extracted document text.
- Provides network and semantic discovery over query or ripgrep-defined universes.

## Requirements

- Windows PowerShell 7 (`pwsh`) for commands in this README.
- Python `>=3.13`.
- `uv` for dependency and environment management.
- `ripgrep` (`rg`) for full-text search.
- Optional external tools for report rendering and document processing, depending
  on workflow: Pandoc, Quarto, Tectonic, PDF/DJVU tooling.

Install or refresh the Python environment:

```powershell
uv sync --extra test
```

Check the installed CLI:

```powershell
uv run archivum --help
```

## Quick Start

List configured libraries:

```powershell
uv run archivum list
```

Open a library and make it current:

```powershell
uv run archivum library-open uber-library
```

Inspect the current library:

```powershell
uv run archivum status
uv run archivum stats
uv run archivum library-config
```

Launch the web interface:

```powershell
uv run archivum serve -b
```

By default the server listens on `http://127.0.0.1:9124`. Use `--port`,
`--address`, `--debug`, and `--prod` for alternate launch modes:

```powershell
uv run archivum serve uber-library --port 9124 --address 127.0.0.1 -b
uv run archivum serve uber-library --prod --address 0.0.0.0
```

## Core Workflows

### Find And Read

Search references with the query engine:

```powershell
uv run archivum q "title ~ /risk measure/ top 20"
uv run archivum query
```

Find by tag, title, or file hash:

```powershell
uv run archivum tag Wang2024 -o
uv run archivum title "spectral risk"
uv run archivum hash 100f150a -o
```

Search extracted document text with ripgrep:

```powershell
uv run archivum rg "spectral risk measure"
uv run archivum rg "capital allocation" -i
```

Check whether a local file already exists in the library:

```powershell
uv run archivum find-doc "C:\path\to\paper.pdf"
```

### Import Documents

Stage a folder of new documents. This hashes files, checks for duplicates,
extracts metadata, and writes a review BibTeX file.

```powershell
uv run archivum stage-docs "C:\S\PDFs\Batch6"
```

Import a reviewed BibTeX file. Without `-x`, this is a dry run.

```powershell
uv run archivum import-bibtex "C:\S\PDFs\Batch6\bibtex-import.bib"
uv run archivum import-bibtex "C:\S\PDFs\Batch6\bibtex-import.bib" -x
```

Useful import options:

- `stage-docs -nf`: skip duplicate checking during staging.
- `stage-docs -d`: delete duplicate files from the source folder if found.
- `import-bibtex -p <dir>`: point to the directory containing referenced docs.
- `import-bibtex -nt`: skip automatic text extraction.
- `import-bibtex -v`, `-vv`, `-vvv`: increase diagnostics.

Link existing library objects when needed:

```powershell
uv run archivum link-doc 100f150a -x
uv run archivum link-tag-hash Wang2024 100f150a
```

### Maintain A Library

Audit structure without changing data:

```powershell
uv run archivum library-audit -v
```

Validate and optionally fix specific structural issues:

```powershell
uv run archivum library-validate --task sharding
uv run archivum library-validate --task sharding -x
uv run archivum library-validate --task orphans -x
uv run archivum library-validate --task missing
```

Manage extracted text:

```powershell
uv run archivum extract-text --help
```

Edit or delete references deliberately:

```powershell
uv run archivum edit-tag Wang2024
uv run archivum delete-tag Wang2024
```

## Web Interface

The web interface is launched by `archivum serve` and is built with Flask,
Bootstrap 5, and HTMX-style incremental updates. It is intended as the primary
day-to-day interface for searching, reading, ingesting, and exploring the
library.

Current screens include:

- Query: fast metadata search with recent/read/random shortcuts, list/table/
  verbose modes, CSV export, report handoff, and live interpretation feedback
  for plain-text fuzzy searches versus explicit querexfuzz expressions.
- Ripgrep: streaming full-text search over extracted document text, summary and
  detail views, context controls, caching, and CSV export.
- Authors: author-centered bibliography browsing.
- Edit: admin metadata editor for BibTeX-backed tag updates.
- Ingest: admin workbench for staging a document, editing source BibTeX,
  previewing the real importer result, and committing to the library.
- Reports: Report Studio for persistent `.qmd` research reports, Pandoc HTML,
  Quarto PDF generation, and cached artifacts.
- Network: co-author social graphs and semantic discovery over selected query
  or ripgrep universes.
- Status: library identity, database counts, file sync state, and watcher state.
- Help: in-app usage notes for query syntax and screen behavior.

The app uses split-horizon access control. Local or trusted traffic can receive
admin capabilities, while external access can be restricted to read-only mode.

Semantic discovery uses `all-MiniLM-L6-v2`, caches the transformer model under
`%LOCALAPPDATA%\archivum\models\sentence-transformers`, and caches embeddings per
library/source so repeated runs avoid unnecessary encoding.

## Data Model

Archivum separates references, documents, and links.

- A reference is bibliographic metadata keyed by a tag such as `Author2024`.
- A document is a physical file identified by content hash and version.
- A ref-doc row links a tag to a document hash/version.

The `Library` class in `src/archivum/library.py` is the central data access layer.
It loads and saves the Feather files, resolves configured paths, synchronizes
BibTeX, runs ripgrep over extracted text, and provides import and audit helpers.

Documents are stored in a sharded directory structure. Internal metadata uses
relative paths where possible so libraries remain portable across machines and
drive mappings.

## Configuration

Global configuration lives under local app data:

```text
%LOCALAPPDATA%\archivum\global-config.yaml
```

Library-specific configuration lives in each library directory:

```text
%LOCALAPPDATA%\archivum\libraries\<library-name>\config.yaml
```

Important configured concepts:

- `default_library`: library opened when no explicit name is supplied.
- `doc_store_lib`: shared document store root.
- `bibtex_file`: synchronized BibTeX path for a library.
- query, enhancement, timezone, table, extractor, and tag-mapping defaults.

Use the CLI to inspect the active configuration:

```powershell
uv run archivum library-config
```

## Development And Tests

Use `uv` for dependency management:

```powershell
uv lock
uv sync --extra test
```

`pyproject.toml` owns the package version. Bump `project.version` after every
code change using semantic versioning; `archivum.__version__` reads the installed
package metadata at runtime.

Run focused web tests through the project runner:

```powershell
.\scripts\Test-ArchivumWeb.ps1 -Mode Fast
.\scripts\Test-ArchivumWeb.ps1 -Mode Slow
.\scripts\Test-ArchivumWeb.ps1 -Mode All
```

The runner defaults to `uv run --extra test pytest`.

Direct pytest examples:

```powershell
uv run --extra test pytest -q
uv run --extra test pytest -m "web and not slow and not browser"
uv run --extra test pytest -m "web and slow and not browser" --run-slow-web
```

Browser tests require Playwright browser support:

```powershell
uv run --extra test python -m playwright install chromium
uv run --extra test pytest -m "web and browser" --run-browser --run-slow-web
```

Many web tests require a configured active Archivum library. Slow semantic tests
can use model and embedding caches and may take longer on a cold machine.

## Safety Notes

- Do not delete source documents unless the exact operation is intentional.
- Be careful around hardlinks, sharded document storage, Feather files, and
  BibTeX synchronization.
- Prefer dry runs first. Many commands require `-x` or `--execute` before they
  write changes.
- Multiple libraries may share the same physical document store.
- Keep web help in `src/archivum/web/templates/help.html` aligned with user-facing
  web behavior.

## Related References

- Query engine: `querexfuzz`
- Full-text engine: `ripgrep`
- Web app entry point: `src/archivum/web/app.py`
- CLI entry point: `src/archivum/cli.py`
- Core library model: `src/archivum/library.py`
