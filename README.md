# Archivum Project

Latin for "archive".

PDF reference manager.

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
