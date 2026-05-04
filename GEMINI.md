# Gemini Project Context

## User Profile
- Expert Python programmer.
- PhD in Mathematics / Qualified Actuary.
- Use `uv` for all pip installs and environment management.

## Coding Rules
- Always use `pathlib.Path` for file manipulations.
- Provide Windows PowerShell or CMD scripts only.
- Use SI units for all calculations.
- Use ISO 8601 dates (YYYY-MM-DD).
- You are an expert Windows Automation Engineer. Every script, command-line snippet, or one-liner you provide MUST be written in PowerShell 7 (pwsh). Never provide Bash, Zsh, or standard Windows CMD unless specifically asked. Use pathlib for all Python file manipulations.

# Archivum Project - Architectural Overview

Archivum is a personal document and reference management system (similar to Mendeley or Zotero), designed for managing papers, books, and bibliographic references.

## Core Architecture

### 1. Data Storage & Management
- **Library Concept**: The project is organized around "Libraries". Each library is a self-contained directory.
- **Location**: Libraries are stored in the user's local app data directory:
    - Windows: `%LOCALAPPDATA%\archivum\libraries\<lib_name>`
    - Unix: `~/.local/share/archivum/libraries/<lib_name>/`
- **Global Configuration**: `global-config.yaml` in the app data directory stores app-wide settings:
    - `doc_store_lib`: The root directory for the sharded document library.
    - `default_library`: The name of the library to open by default.
- **Data Formats**:
    - **Metadata**: Stored as Pandas DataFrames in `.feather` files for high-performance reading/writing.
        - `ref.feather`: Bibliographic reference data.
        - `doc.feather`: Document file metadata (path, hash, etc.).
        - `ref-doc.feather`: Junction table mapping references (`tag`) to physical files (`path`).
    - **Library Config**: `config.yaml` stores library-specific settings (name, description, bibtex_file).
    - **BibTeX**: A `bibtex.bib` file is automatically generated and kept in sync with the reference database.
- **Core Class (`Library`)**: The `Library` class in `src/archivum/library.py` is the central hub for data access. It lazily loads DataFrames and provides methods for querying, saving, and auditing.

## Configuration Hierarchy & Ownership

### 1. Global Config (`global-config.yaml`)
- **Location**: `%LOCALAPPDATA%\archivum\global-config.yaml`
- **Ownership**: Controls the CLI environment and provides global defaults for all libraries.
- **CLI/Env Fields**:
    - `doc_store_lib`: Root directory for the sharded document library (Default: `sharded-library`).
    - `default_library`: Name of the library to open by default.
    - `debug_dir`: Directory for audit/debug logs (Default: `debug`).
    - `theme`: UI theme (`system`, `light`, `dark`).
- **Library Default Fields**: Includes shared policies such as `ref_columns`, `enhancement_strategies`, `timezone`, `tablefmt`, `extractor`, `hash_workers`, and `tag_name_mapper`.

### 2. Library Config (`config.yaml`)
- **Location**: `.../archivum/libraries/<lib_name>/config.yaml`
- **Ownership**: Library-specific identity. It only needs to contain overrides for the global defaults.
- **Strict Pydantic Model (`Configurator`)**:
    - `name`: Human-readable name.
    - `description`: Optional text.
    - `bibtex_file`: Absolute path to the synchronized `.bib` file.
    - (Any other field from Global Config can be overridden here).

### 2. Querying Engine
- **Querexfuzz**: Archivum uses a specialized querying engine called `querexfuzz`. It extends Pandas DataFrames with a `.querex()` method, allowing for a combination of regex, SQL-like syntax, and fuzzy matching.
- **Fuzzy Matching**: Uses Rust-based fuzzy matching (`rustfuzz`) for fast completion and searching in the CLI.

### 3. Command Line Interface (CLI)
- **Framework**: Built with `click`.
- **Primary Entry Point**: `src/archivum/cli.py`.
- **Uber Shell**: A powerful, interactive REPL (`uber` command) built using `prompt_toolkit`. It provides:
    - Context-aware fuzzy autocompletion (for tags, titles, and library names).
    - Integrated search and document opening.
    - History and status reporting.

### 4. Web Interface
- **Framework**: Built with `Flask` and `HTMX`-style dynamic updates.
- **Entry Point**: `src/archivum/web/app.py` (launched via `archivum serve`).
- **Features**:
    - **Search**: Interactive `querexfuzz` search interface with live results.
    - **Ripgrep Integration**: Full-text search across the document library with highlighted snippets and context.
    - **Document Viewer**: Integrated PDF viewer for directly opening documents from the browser.
    - **Command Execution**: Limited execution of CLI commands via a web-based terminal emulator.
    - **Status & History**: Real-time view of library statistics and command history.

### 5. Document Processing & Metadata Discovery
- **Metadata Extraction (`Document` class)**: Located in `src/archivum/document.py`. It uses a Gather -> Rank -> Verify strategy:
    - **Gather**: Collects info from filenames, PDF metadata (MuPDF), and visual OCR (finding the largest font for titles).
    - **Enhance**: Performs lookups against external APIs (Crossref, Arxiv).
    - **Verify**: Reconciles multiple sources to produce high-confidence bibliographic data.
- **Full-text Search**: Extracts text from PDFs (via `pdftotext` or `pymupdf`) and provides a search interface using `ripgrep` (`rg` command).

### 6. Key Modules
| Module | Description |
| :--- | :--- |
| `library.py` | Core data management, DataFrame handling, and persistence. |
| `cli.py` | Command-line interface and interactive Uber Shell. |
| `web/` | Flask-based web interface (routes, templates, and app factory). |
| `document.py` | PDF processing, metadata discovery, and text extraction. |
| `gui.py` | Tkinter-based metadata editor. |
| `reference.py` | Data structure for bibliographic entries. |
| `config.py` | Pydantic-based configuration management. |
| `import_bibtex.py` | Logic for incremental imports from `.bib` files. |
| `bibtex.py` | Conversion utilities between internal dicts and BibTeX strings. |
| `utilities.py` | Shared helper functions (tag allocation, path sanitization). |

## Dependencies
- **Data**: `pandas`, `pyarrow` (feather).
- **CLI/UI**: `click`, `prompt_toolkit`, `rich`, `uber_shell`.
- **Web**: `flask`, `jinja2`.
- **PDF/Metadata**: `pymupdf` (fitz), `nameparser`, `rapidfuzz`.
- **Search**: `ripgrep` (external dependency).
- **Core Utils**: `pydantic`, `pyyaml`, `pendulum`, `lark`.

## Workflow
1. **Create/Open**: Use `create` or `open` to set the active library.
2. **Import**: Bring in new docs via `import-bibtex` or `import-doc`.
3. **Query**: Use the `query` command, `uber` shell, or `serve` (web interface) to find documents.
4. **Open**: Documents can be opened directly from the CLI/REPL or viewed in the Web Interface.
5. **Sync**: Changes are saved back to `.feather` files and the `.bib` file.

## Design Philosophy
- **Non-Destructive**: Archivum should never autonomously delete source files. Sharding and organization operations use hardlinks to ensure the original files remain untouched in their source locations. Deletion is only permitted via explicit user commands or when explicitly confirmed (e.g., during duplicate cleanup in `import-doc`).
- **Production Environment**: This is a production system. All operations must be performed with extreme care. Avoid "hacking" or experimental changes that could corrupt the library metadata or the document store.
- **Shared Document Store**: Multiple libraries share a single physical sharded document store (`doc_store_lib`). Be aware that different libraries might have slightly different metadata for the same file hash, which could lead to "fighting" over the canonical filename.

## TODOs
- **Logging**: Refine and standardize logging output for `import-doc` and `import-bibtex`.
- **Entry Editing**: Implement an easy way to edit reference entries directly from the CLI (e.g., fixing tags with missing years like "Delaen").
- **hash** command: use first 12 of hash not whole string.
- Review the bibtex file build - seems some nans and non-unicode dashes creeping in.
- bibtex: {Frezal2017, title       = {{Insurance Regulation: the 1-year 99.5}}, is broken
- bibtex: P\cedzich2016
- @article{Scott2009,
- need a way to put in a ref with no doc - eg Simon Convexity is missing.
- Rename a library (test library!!)
- ?Mass scan of pdfs for new docs?
- https://acrobat.adobe.com/link/home/


## Web Interface Overhaul (May 2026)

The web interface (`archivum serve`) has been significantly enhanced to provide a production-grade library management experience.

### 1. Architectural Changes
- **Bootstrap 5 Integration**: Migrated the entire front-end to Bootstrap 5 for a responsive, modern UI.
- **HTMX & Streaming**: Implemented a streaming search engine using Flask's `Response` generators and HTMX's Out-of-Band (OOB) swaps.
- **State Management**: Improved library auto-recovery from environment variables if the session context is lost.

### 2. Key Features
- **Streaming Ripgrep**: Full-text results are now streamed to the browser as they are found, providing near-instant feedback even for massive libraries. Optimized with `--line-buffered` and metadata caching.
- **Standalone Tag Editor**: A new "Edit" tab with a high-density, live-filterable sidebar for selecting tags and editing BibTeX metadata directly in the browser.
- **Search History**: Added terminal-style history (Up/Down arrows) and a "Clear" icon to all search inputs.
- **Enhanced Status**: Full visibility into the underlying `.feather` database files, including disk modification times and synchronization status.
- **Mobile Responsive**: Fully collapsible navbar and reflowing control bars for use on phones and tablets.

## Technical Implementation Details
- **Ripgrep Streaming**: The `rg_search` route in `routes.py` uses a generator to yield HTML chunks as JSON events are parsed from the `rg` process. These chunks use `hx-swap-oob="beforeend:#rg-results"` to append results without a full page refresh.
- **Tag Uniqueness**: The editor handles tag changes by updating the junction table (`ref_doc.feather`) and verifying that new tags do not already exist before persisting.
- **CSS Density**: Custom CSS in `style.css` enforces high-density layouts (3rem hanging indents for lists, 1.5rem for ripgrep) while maintaining readability.

# File Organizaion

* Definitive library is \s\ShardedDocLibrary
* On Kolmogorov old Books, Book_scans, and Library are in ~\RawDocs and are Linked back to their original locations in S. These links will obviously work only on Kolmogorov.
* The two copies are hard links of one another - spot checked and seems to work.
* Since sharded doc lib is in S it syncs to Google Drive (which means VPS has access in theory...)
