# Archivum Project

Latin for "archive".

Reference manager for content-addressable document libraries.

## Core Features

- **Identity-Based Linking**: References are linked to documents by content hash and version, not by volatile file paths.
- **Library Portability**: All internal paths are stored relative to the library root. Move your library anywhere, and it just works.
- **Smart Sharding**: Automatically organizes documents into a hash-based directory structure with rich, informative names.
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

## General STUFF

```python
# basic imports
from pathlib import Path
import pandas as pd
import re
import numpy as np
from functools import partial
import pprint as pp
from random import sample

import fitz  # PyMuPDF
import Levenshtein

from archivum.utilities import fGT
import archivum.cli as cli
import archivum.crossref as arcc
import archivum.document as arcd
import archivum.gui as arcg
import archivum.library as arcl
import archivum.mendeley_port as arcm
import archivum.parser as arcp
import archivum.reference as arcr
import archivum.utilities as arcu


%load_ext autotime
```

## Test GUI

```python
cli.entry(args=['--help'], standalone_mode=False);
```

Produces

```
Usage: ipykernel_launcher.py [OPTIONS] COMMAND [ARGS]...

  CLI for managing bibliographic entries.

Options:
  --help  Show this message and exit.

Commands:
x  close-library        Close the currently open library.
  create-library       Interactively create a YAML config file for a new...
x  get-distinct-values  Display number of distinct values in each library...
x  get-library-stats    Display library stats library.
  import               Import bibliographic entries, optionally filtered...
x  list-libraries       List all available libraries.
  merge-library        Merge another library into the current library.
  new                  Scan a directory for new PDF files and optionally...
x  open-library         Open a library by name and set it as current.
  query-library        Interactive REPL to run multiple queries on the...
x  save-library         Save the current library to disk.
  uber                 Start an interactive REPL loop for issuing...
```

`query-library` won't work for `asyncio` reasons.

```python

cli.entry(args=["list-libraries"], standalone_mode=False)
cli.entry(args=["list-libraries", "-d"], standalone_mode=False)


cli.entry(args=['open-library', 'uber-library'], standalone_mode=False)
cli.entry(args=['get-library-stats'], standalone_mode=False)
cli.entry(args=['get-distinct-values', ], standalone_mode=False)
cli.entry(args=['get-distinct-values', '-f author'], standalone_mode=False)
cli.entry(args=['close-library', ], standalone_mode=False)
cli.entry(args=['get-library-stats'], standalone_mode=False)

# NYI
cli.entry(args=['save-library'], standalone_mode=False)
cli.entry(args=['merge-library', 'othername'], standalone_mode=False)
cli.entry(args=['create-library', 'Test Library'], standalone_mode=False)
cli.entry(args=['import'], standalone_mode=False)
cli.entry(args=['new'], standalone_mode=False)
cli.entry(args=['new', '-m'], standalone_mode=False)
```


## Create a Library

```python
lib = arcl.Library('uber-library')
fGT(lib.stats())
fGT(lib.stats_ref_fields())
fGT(lib.database.querex('top 10 recent author ~ /Wang, R/'))
fGT(lib.database.querex('top 10 recent ! /Wang, R/'))
fGT(lib.ref_df.querex('top 10 recent title ~ /Risk Measure/'))
```



## CrossRef Reference Search

`search`, `search_by_title`, and `lookup_doi`.

```python
r = arcc.search(author='Stephen Mildenhall', rows=5)
pp(r[0])

r = arcc.search(title="Heegner points and derivatives of L-series", rows=1)
pp(r)

r = arcc.search(author='Stephen Mildenhall', rows=5)
print(len(r))

for _ in r:
    ref = arcr.Reference.from_crossref(_, lib)
    pp(ref.to_dict())
```

### DOIs

Format

```
10.{registrant}/{suffix}
```

BibTex optionally with the URL but *only one*.

```json
doi = {10.1038/nphys1170},
url = {https://doi.org/10.1038/nphys1170},
```

Resolve with `https://doi.org/{doi}`.


## Extract Meta Data

```python
text_dir_name='\\temp\\pdf-full-text'
extractor='pdftotext'
pDocument = partial(arcd.Document, text_dir_path=Path(text_dir_name), extractor=extractor)
ps = lib.doc_df.sample(200).path

ans = []
for p in ps:
    p = Path(p)
    d = pDocument(p)
    a = d.meta_data(lib)
    ans.append([p.name, a.author, a.author_ex, a.subject, a.title, a.raw])

df = pd.DataFrame(ans, columns=['name', 'author', 'author_ex', 'subject', 'title', 'raw'])
fGT(df)
```

## Extract text from pdfs

Use `def find_pdfs(*dir_names)` to find PDFs. These functions create text files in the `qaffo` naming format.

Use `def find_missing_txt(pdf_paths, text_dir_name='\\temp\\pdf-full-text', extractor='pdftotext')`



```python
pdfs, docs, result = arcd.pdf_dir_to_text('\\S\\Library')
print(f'AUDIT: {len(docs) = }, {len(result.failure) = }, {len(result.success) = }, check = {len(docs) - (len(result.failure)+len(result.success))}')

pdfs, docs, result = arcd.pdf_dir_to_text('\\S\\Books')
print(f'AUDIT: {len(docs) = }, {len(result.failure) = }, {len(result.success) = }, check = {len(docs) - (len(result.failure)+len(result.success))}')

pdfs, docs, result = arcd.pdf_dir_to_text('\\S\\Scans\\Book_scans')
print(f'AUDIT: {len(docs) = }, {len(result.failure) = }, {len(result.success) = }, check = {len(docs) - (len(result.failure)+len(result.success))}')

```

Produces

* 6049 + 85 = 6132
* 316 + 0
* 412 + 0

PDF / text files respv.

## sdf


```python

```

## sdf


```python

```


## Steps

1. Port Mendeley library


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


## Porting an Existing Mendeley Library

* References are BibTeX entries, creates  `ref_df`
* Part of a Mendeley bibtex entry is a field `file` that is a `;` separated list of a `:` list of `drive:path:suffix`. These paths may or may not exist, call them `vfiles` (virtual files, like a `Path` object to a file that DNE). These are extracted into `proto_ref_doc_df`, the prototype reference-document table.
* Separately we have documents corresponding to actual files, `afiles`, found by rgrepping the relevant Library directory
* A reference can have zero or more corresponding `vfiles`
* Need to match `vfiles` to `afiles`. This is done with fuzzy name matching and the Levenshtein library to compute distance resulting in `best_match_df` from which we create `best_match_mapper`
* `ref_doc_df` then effects the remapping.


# Querex Language

## Test Cases

Run against `ref_df`

```python

querex_test_cases = [
    '',
    'top 4',
    'recent',
    'recent top 3',
    'verbose recent top 17',
    'select *',
    'top 10 select *',
    'where year == 2024',
    'where year == "2024"',
    'where type == "book"',
    '! Delbaen',
    '! /Wang, R/',
    '! /Wang, R',
    'recent top 3 author ~ /Wang, R/',
    'verbose top 5 recent select journal author ~ /Wang, R/',
    'top 6 order author',
    'top 7 order journal',
    'verbose top 8 select journal where year == "2024" order author',
    'verbose top 9 select journal where year == "2024" order -journal, author',
]


import archivum.parser as arcp
import archivum.library as arcl
from archivum.utilities import fGT
lib = arcl.Library('uber-library')

for q in querex_test_cases:
    print(repr(q))
    try:
        r = lib.ref_df.querex(q)
    except ValueError as e:
        print('ERROR: ', e)
    else:
        # print(sorted(r.columns))
        display(fGT(r.head(10)))
```
