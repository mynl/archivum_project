# Gemini Project Context

## User Profile
- Expert Python programmer.
- PhD in Mathematics / Qualified Actuary.
- Prefer concise, direct answers without prefaces.

## Coding Rules
- Always use `pathlib.Path` for file manipulations.
- Provide Windows PowerShell or CMD scripts only.
- Use SI units for all calculations.
- Use ISO 8601 dates (YYYY-MM-DD).

# Archivum Project - Architectural Overview

Archivum is a personal document and reference management system (similar to Mendeley or Zotero), designed for managing papers, books, and bibliographic references.

## Core Architecture

### 1. Data Storage & Management
- **Library Concept**: The project is organized around "Libraries". Each library is a self-contained directory.
- **Location**: Libraries are stored in the user's local app data directory:
    - Windows: `%LOCALAPPDATA%\archivum\libraries\<lib_name>`
    - Unix: `~/.local/share/archivum/libraries/<lib_name>/`
- **Data Formats**:
    - **Metadata**: Stored as Pandas DataFrames in `.feather` files for high-performance reading/writing.
        - `ref.feather`: Bibliographic reference data.
        - `doc.feather`: Document file metadata (path, hash, etc.).
        - `ref-doc.feather`: Junction table mapping references (`tag`) to physical files (`path`).
    - **Configuration**: `config.yaml` stores library-specific settings (paths, formats, etc.).
    - **BibTeX**: A `bibtex.bib` file is automatically generated and kept in sync with the reference database.
- **Core Class (`Library`)**: The `Library` class in `src/archivum/library.py` is the central hub for data access. It lazily loads DataFrames and provides methods for querying, saving, and auditing.

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

### 4. Document Processing & Metadata Discovery
- **Metadata Extraction (`Document` class)**: Located in `src/archivum/document.py`. It uses a Gather -> Rank -> Verify strategy:
    - **Gather**: Collects info from filenames, PDF metadata (MuPDF), and visual OCR (finding the largest font for titles).
    - **Enhance**: Performs lookups against external APIs (Crossref, Arxiv).
    - **Verify**: Reconciles multiple sources to produce high-confidence bibliographic data.
- **Full-text Search**: Extracts text from PDFs (via `pdftotext` or `pymupdf`) and provides a search interface using `ripgrep` (`rg` command).

### 5. Key Modules
| Module | Description |
| :--- | :--- |
| `library.py` | Core data management, DataFrame handling, and persistence. |
| `cli.py` | Command-line interface and interactive Uber Shell. |
| `document.py` | PDF processing, metadata discovery, and text extraction. |
| `reference.py` | Data structure for bibliographic entries. |
| `config.py` | Pydantic-based configuration management. |
| `import_bibtex.py` | Logic for incremental imports from `.bib` files. |
| `bibtex.py` | Conversion utilities between internal dicts and BibTeX strings. |
| `utilities.py` | Shared helper functions (tag allocation, path sanitization). |

## Dependencies
- **Data**: `pandas`, `pyarrow` (feather).
- **CLI/UI**: `click`, `prompt_toolkit`, `rich`, `uber_shell`.
- **PDF/Metadata**: `pymupdf` (fitz), `nameparser`, `rapidfuzz`.
- **Search**: `ripgrep` (external dependency).
- **Core Utils**: `pydantic`, `pyyaml`, `pendulum`, `lark`.

## Workflow
1. **Create/Open**: Use `create` or `open` to set the active library.
2. **Import**: Bring in new docs via `import-bibtex` or `import-doc`.
3. **Query**: Use the `query` command or `uber` shell to find documents.
4. **Open**: Documents can be opened directly from the CLI/REPL using their `tag` or `title`.
5. **Sync**: Changes are saved back to `.feather` files and the `.bib` file.

## TODOs
- **Logging**: Refine and standardize logging output for `import-doc` and `import-bibtex`.
- **Entry Editing**: Implement an easy way to edit reference entries directly from the CLI (e.g., fixing tags with missing years like "Delaen").
- **hash** command: use first 12 of hash not whole string.
