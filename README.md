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
