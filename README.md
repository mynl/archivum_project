# Archivum Project

Latin for "archive".

PDF reference manager.

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

import archivum.mendeley_port as arcm
import archivum.library as arcl
import archivum.utilities as arcu
import archivum.document as arcd
import archivum.crossref as arcc
import archivum.gui as arcg
import archivum.reference as arcr
from archivum.utilities import fGT

%load_ext autotime
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
