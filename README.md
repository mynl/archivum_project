# Archivum Project

Latin for "archive".

Reference manager for content-addressable document libraries.

## Quick Start (2026 Workflows)

### 1. New Document Import
The standard workflow for adding a batch of new PDFs:
*   **Stage**: `stage-docs <dir>` - Hashes files, checks for duplicates, extracts metadata, and opens a `.bib` file in your editor for review.
    *   `-f / -nf`: Toggle duplicate checking (default: True).
    *   `-d`: Delete duplicates from the source folder immediately if found.
*   **Import**: `import-bibtex <file.bib> -x` - Reads the reviewed `.bib`, creates the reference, and **shards** the document into the library.
    *   `-t / -nt`: Toggle automatic text extraction for search (default: True).

### 2. Linking & Editing
*   **Link Orphan**: `link-hash <hash_prefix> -x` - Find a document in the library that has no reference, discover its metadata, and create a new reference for it.
*   **Link Reference**: `link-tag-hash <tag> <hash_prefix> -x` - Link an existing reference (tag) to an existing document hash.
*   **Edit Reference**: `edit-tag <tag>` - Open a reference entry for interactive editing.

### 3. Finding & Searching
*   **By Tag**: `tag -o` - Search for a tag. Use `-o` to open the associated document immediately.
*   **By Hash**: `hash <hash_prefix> -o` - Search by file hash. Displays linked references or labels it as an "Orphan".
*   **Web Interface**: `serve` - Launch the interactive web interface for search, browsing, and reading.
*   **Find Local**: `find-doc <path>` - Hash a local file and see if it (or any matches) exists in your library.

### 4. Maintenance
*   **Audit**: `library-audit -v` - Report structural issues (orphans, missing files, broken links).
*   **Validate**: `library-validate --task <task> -x` - Fix issues found by the audit (e.g., sharding inconsistencies).

---

## Core Features

- **Identity-Based Linking**: References are linked to documents by content hash and version, not by volatile file paths.
- **Library Portability**: All internal paths are stored relative to the library root. Move your library anywhere, and it just works.
- **Smart Sharding**: Automatically organizes documents into a hash-based directory structure with rich, informative names.
- **Modern Web Interface**: A Flask and HTMX-powered web interface for querying, full-text search (ripgrep), and integrated document viewing.
- **Powerful Search**: Multi-modal search using `querexfuzz` (regex + SQL-like) and full-text search via `ripgrep`.

---

## 2026 Workflow: Adding New Documents

The current robust workflow for adding new batches of documents consists of staging, review, and final import.

### 1. Staging and Metadata Extraction
Gather your new PDF/DJVU files into a staging directory (e.g., `C:/S/PDFs/Batch6`). Run the following command to identify duplicates already in your library and prepare a BibTeX file for review:

```bash
archivum stage-docs C:/S/PDFs/Batch6
```

*   **`--flag-duplicates` (default True)**: Automatically hashes all files and checks them against your library.
*   **`--delete` (optional)**: If duplicates are found, it offers to delete them from your staging folder.
*   **Result**: It generates `bibtex-import.bib` in the staging folder and opens it in **Sublime Text**. You can review and edit the tags, titles, and authors here.

### 2. Final Import and Organization
Once you are happy with the `.bib` file, perform the final import. This step handles deduplication, re-mapping tags to standard `AuthorYYYY[a-z]` format, and **sharding** the physical files into the library's document store.

```bash
archivum import-bibtex C:/S/PDFs/Batch6 --execute
```

*   **Guardian Mode (default True)**: Performs a final hash check. If both the file hash and metadata match an existing entry, the import is skipped.
*   **Identity Linking**: Creates links based on the file's hash and version.
*   **Automatic Sharding**: Files are automatically hardlinked into the library's sharded directory structure (`/00/` to `/FF/`) with rich canonical names (`Hash_Year_Author_Title.pdf`).

### 3. Maintenance and Cleanup
Use these tools to keep your library in perfect shape:

```bash
# Verify sharding and fix metadata inconsistencies
archivum validate --task sharding --execute

# Find and fix orphaned documents added recently
archivum reconnect --max-age 28 --execute

# Manually link a tag to a document hash
archivum link NAIC2023 100F150A6 --version 0

# Check if a local file exists in the library
archivum find-doc "\path\to\paper.pdf"
```

---

## Web Interface

Archivum includes a powerful web interface built with Flask, Bootstrap 5, and HTMX.

### Key Features
- **Real-time Search**: Instant filtering of references using fuzzy matching and `querex` expressions.
- **Streaming Ripgrep**: Blazing fast full-text search that streams results to the browser as they are found. Supports complex filters like context lines, case sensitivity, and glob patterns.
- **Report Studio**: A powerful environment for synthesizing research. Transform search results into persistent, professional journals with custom introductions and Markdown notes. Supports integrated HTML viewing and archival PDF generation (via Quarto/Tectonic).
- **Smart Caching**: High-performance rendering pipeline. Automatically caches HTML and PDF versions of reports to provide near-instant retrieval on subsequent views.
- **Split-Horizon Security**: Intelligent IP-based access control. Automatically grants full **Admin** rights to local/VPN traffic while restricting external/VPS traffic to a **Read-Only** mode (indicated by a header badge).
- **High-Density UI**: Specialized "split-pane" layouts for Authors and Editor pages with independent scroll containers for sidebars and content.
- **Responsive Design**: Fully mobile-ready with a collapsible navbar and reflowing control bars.
- **Search History**: Terminal-style query recall using Up/Down arrows in all search boxes.
- **Library Status**: Deep visibility into database integrity and file synchronization.

### Usage
Launch the web interface from the terminal:
```powershell
archivum serve
```
By default, this serves the library at `http://127.0.0.1:9124`. Use the `--browser` (or `-b`) flag to open it automatically.

### Architecture
The web layer is designed for high performance and low overhead:
- **HTMX Integration**: Most interactions are handled via HTMX, providing a "Single Page App" feel without the complexity of a heavy frontend framework.
- **Response Streaming**: The Ripgrep engine uses HTTP chunked transfer encoding and HTMX OOB (Out-of-Band) swaps to provide immediate visual feedback during long-running searches.
- **Bootstrap 5**: Provides the structural grid and responsive components, customized for a high-density, professional look.
- **Local Storage**: Search history is persisted in the browser's local storage for a consistent user experience across reloads.

---

## Architecture & Data Model

Archivum is built on a content-addressable model where file identity is king.

### Data Storage
- **`ref.feather`**: Bibliographic metadata (tags, authors, titles, etc.).
- **`doc.feather`**: Document index mapping `(hash, version)` to a `relative_path`.
- **`ref-doc.feather`**: Junction table linking `tag` to `(hash, version)`.

### Path Resolution
The system uses a lazy de-relativization strategy. Paths are stored as clean relative strings (e.g., `0a/Hash...pdf`). The `Library` class resolves these to absolute paths (using your configured `doc_store_path`) only when touching the disk, caching the results for performance. This allows libraries to be shared across devices with different drive mappings (e.g., `S:\` vs `C:\Users\steve\...`) seamlessly.

## Lark

Parser IDE: https://www.lark-parser.org/ide/


## Bibtex format

| Field       | Status   | Typical Use in Journals                             |
|-------------|----------|-----------------------------------------------------|
| author      | Keep     | Required for almost all citation styles             |
| title       | Keep     | Always shown in article/book citations              |
| journal     | Keep     | Needed for articles (appears in most styles)        |
| booktitle   | Keep     | Used for conference proceedings                     |
| year        | Keep     | Always required                                     |
| volume      | Keep     | Needed for journal articles                         |
| number      | Keep     | Issue number, often shown next to volume            |
| pages       | Keep     | Required for most styles                            |
| publisher   | Keep     | Required for books and proceedings                  |
| doi         | Keep     | Increasingly shown as hyperlink                     |
| url         | Maybe    | Shown in some styles, especially for online-only    |
| note        | Maybe    | Sometimes shown, often free-form                    |
| annote      | Drop     | Personal notes, never shown in output               |
| abstract    | Drop     | Used internally, not for citation                   |
| file        | Drop     | Path to PDF, not part of citation                   |
| keywords    | Drop     | Useful for search, not shown in citation            |
| month       | Maybe    | Occasionally shown, but rarely required             |
| eprint      | Maybe    | Used for preprints (e.g. arXiv)                     |
| institution | Maybe    | Used for tech reports and theses                    |
| editor      | Maybe    | Required for edited volumes                         |
| series      | Maybe    | Sometimes used for book series                      |
| isbn        | Maybe    | Occasionally used for books                         |
| issn        | Drop     | Rarely shown in citation styles                     |
| language    | Drop     | Not typically cited                                 |

