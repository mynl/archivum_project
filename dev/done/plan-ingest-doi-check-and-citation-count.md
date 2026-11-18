# Ingest: DOI check before a reference is added, and its citation count after

Drafted 2026-11-18. Executed 2026-11-18 as 2.16.0a18; see Execution notes at the foot.

## Goal

When a reference is added on the Ingest page, check that the DOI in the entry being committed
belongs to that entry, before anything is written. If the title and first author agree with
the source's record of the DOI, add the reference without further questions. If they do not,
show a warning and ask for confirmation, in the same way the page already does for a duplicate.
After the reference is added, attach its citation count and report the DOI result and the
count in the result banner the page already shows.

The request came from the first full count run (2026-11-17), where 26 references held junk
DOIs given to them by a reference manager and showed another work's 24,747 citations. Ingest
is where a wrong DOI enters the library. The check also covers a second case: the committed
entry is often pasted from Google Scholar over the suggested entry, so the DOI checked must be
the one in the text committed, never the one in the suggestion.

## Current behavior

All of this is in `src/archivum/web/routes/ingest.py` and `Library` (`src/archivum/library.py`).

- **Two commit routes.** `POST /ingest/commit` adds a staged document with its entry;
  `POST /ingest/reference/commit` adds a reference with no document. Both call
  `Library.import_staged_document(bibtex, path_or_None, allow_duplicate=...)`, which runs the
  real importer (`Bib2df_Incremental`), saves, shards, and extracts text.
- **The duplicate confirmation**, which this plan mirrors. `Library.import_conflict` finds a
  file, DOI or title match with the library. The preview template `_ingest_preview.html` shows
  it as an alert with an **Import anyway** button, a form carrying the hidden field
  `import_anyway=on`. A commit without that field raises `LibraryImportBlocked`; the route
  re-renders the workbench with a warning banner and the document is not marked failed.
- **Result banner.** `components/ingest_result.html`, appended to the log under the
  workbench: "`<file>` archived as `<Tag>` View".
- **No DOI check and no count.** A DOI in the entry is stored as typed. The count arrives only
  when **Fetch citation counts** or **Check DOIs** is next run on the Audit page.
- **What exists to build on**, all in `src/archivum/doi_audit/`: `sources.openalex_work(client,
  "doi:<doi>")`, `sources.crossref_work`, `sources.doi_exists` (records with `title`,
  `first_author`, `year`, `cited_by_count`); `doi.match(reference, record).cls`, which gives
  `verified`, `year differs`, `weak match` or `mismatch` (title similarity 0.9 and 0.6, first
  author's surname, years within one); `journal.append_lines` for `observations.jsonl` and
  `counts.jsonl`; `journal.derive_cites`, which rebuilds `cites.feather`. `egress.default()` is
  the process-wide client for interactive lookups, used with `retry=False` and a 10 s timeout
  by `crossref.py`.

## The change

### [check module] `src/archivum/doi_audit/ingest_check.py`, new

Pure logic, no Flask, no `Library`.

`check_entry_doi(bibtex_text, client) -> IngestDoiCheck`, a frozen dataclass:

| field | content |
|---|---|
| `doi` | the entry's DOI, normalized (`doi.normalize_doi`); empty when the entry has none |
| `status` | one of the values below |
| `source` | `openalex`, `crossref`, `doi.org`, or empty |
| `record` | the source's title, first author, year, container, for the warning |
| `score` | title similarity, or `None` |
| `count`, `openalex_id` | OpenAlex's `cited_by_count` and work id, when OpenAlex has the DOI |
| `error` | the failure text when the sources could not be reached |

Steps, stopping at the first that settles it:

1. Parse the first entry with `bibtex_to_dict` (as `Library._first_bibtex_tag` does) for
   `doi`, `title`, `author`, `year`. No DOI: status `no doi`, no request.
2. A value `doi.classify_stored_doi` calls `attention` (not a DOI at all): status `not a doi`,
   no request.
3. OpenAlex, `doi:<doi>`: one request, which also gives the count. A record: class it with
   `doi.match`.
4. OpenAlex has no such work: Crossref, one request, classed the same way. No count.
5. Neither: `doi.org`, one request. Registered: `not compared`. Not registered: `unresolved`.
6. A request that fails or times out: status `not checked`, with the error.

| status | at commit |
|---|---|
| `verified` | go ahead |
| `no doi` | go ahead, nothing to check and no count |
| `not checked` | go ahead; the banner gives the reason. Not expected to occur |
| `year differs`, `weak match`, `mismatch`, `unresolved`, `not compared`, `not a doi` | stop and ask |

Only a full match passes without a question (Steve, 2026-11-18). `year differs` and `not
compared` stop too: in the first the entry's year may be the error, and the warning is the
moment to correct it; in the second there is no record to match.

The `sources` functions call `client.get` with the run's defaults, which retry with backoff for
minutes. An interactive check must not: the module wraps the client so that every `get` is
`retry=False, timeout=10`, the settings `crossref.py` uses. Worst case three requests, 30 s,
when every source is down; the usual case is one request to OpenAlex.

`record_ingest_check(library_path, tag, check)`: after the import, append the observation
lines to `observations.jsonl` under the new tag, with `query_kind="doi"`, exactly as a Check
DOIs run writes them, so that the Audit page shows the reference as checked and the next run
skips it as fresh. With a count, append the line to `counts.jsonl` and call
`journal.derive_cites`. No count is taken from Crossref: `cites` is the OpenAlex figure
everywhere else.

### [routes] `ingest.py`, both commit routes

A helper shared by `ingest_commit` and `ingest_reference_commit`:

1. The duplicate test comes first and sends nothing. If `import_anyway` is not set, call
   `lib.preview_staged_document_import` (the call the preview already makes, which writes
   nothing) and, when it reports a conflict, return the existing warning as now.
2. `check = check_entry_doi(bibtex, egress.default())`.
3. If the status is one that stops, and the form field `doi_confirmed` is not equal to
   `check.doi`: re-render the workbench with the DOI warning, add a warning banner
   ("`<file>`: not imported. The DOI is another work's, by title or first author."), and
   return. Nothing is written and the document is not marked failed.
4. Otherwise import as now, then `record_ingest_check(lib.config_path, tag, check)`, and add
   the DOI sentence to the success banner.

`doi_confirmed` holds the DOI that was confirmed, not `on`: if the DOI is edited after the
warning, the new one is checked again.

A failure inside step 4's recording (journal unwritable) is logged and reported in the banner;
the reference is already added and stays added.

### [templates] `_ingest_preview.html`, `components/ingest_result.html`

- The preview takes an optional `doi_warning`. When present it shows an alert under any
  duplicate alert: the entry's title, first author and year beside the source's, the title
  similarity, the DOI as a link, and one button, **The DOI is right, import**, a form like
  Import anyway with `doi_confirmed=<doi>` (and `import_anyway=on` carried over when it was
  set). The text also says what else can be done: correct or delete the `doi` line in the
  BibTeX and press Import again.
- The warning is rendered only on the response to a commit. Refreshing the preview sends no
  request and shows no DOI warning.
- Banner sentences, after "archived as `<Tag>`":

| status | sentence |
|---|---|
| verified, with count | DOI verified at OpenAlex. Cited 1,234 times. |
| verified at Crossref | DOI verified at Crossref. OpenAlex has no record, so no citation count. |
| confirmed by hand | DOI kept at your word (mismatch). Cited 24,747 times at OpenAlex, for that DOI. |
| confirmed by hand, no record | DOI kept at your word (exists, not compared). No citation count. |
| not checked | DOI not checked: `<error>`. Check DOIs on the Audit page will pick it up. |
| no doi | No DOI, so no citation count. |

### [help and usage page]

`help.html`, Ingest section, in the same commit as the routes. The usage page
`D:\Projects\Using\archivum\doi-citation.qmd` gets a short section "DOI check at Ingest",
rendered to HTML and PDF, as the last step.

## Not in this plan

- **Finding a DOI for an entry that has none.** No DOI, no check, no count (Steve,
  2026-11-18): Ingest's discovery has already asked Crossref for one.
- `POST /ingest/replace` with "update metadata", the Edit page, the CLI `import-bibtex`, and
  the Audit page's Import as a new reference (already checked against Crossref).
- A deny-list of known junk DOIs. The check makes one unnecessary at Ingest.

## Outbound data and cost

- Sent: the DOI, to `api.openalex.org`, then `api.crossref.org`, then `doi.org`, all already
  in `egress.ALLOWED_HOSTS`. Nothing else leaves. `tests/test_egress.py` already enforces the
  host list and that `requests` is used nowhere new.
- Not background work: the only trigger is a person pressing Import. One to three requests a
  commit, each with a 10 s timeout and no retry. No timer, no watcher, no polling.
- Writes: one or two lines appended to `observations.jsonl`, one to `counts.jsonl`, and
  `cites.feather` rebuilt once. The rebuild reads the whole of `counts.jsonl` (2,831 counted
  references on 2026-11-17); its time is to be measured at execution and stated in the log.
  The web's `refresh_cites` sees the new file on the next request by one `stat`, as now.
- A DOI audit run in progress appends to the same journals. Appends are single writes under
  the journal lock and `cites.feather` is replaced atomically, so the two do not corrupt each
  other; a run's own rebuild at its end includes the ingest's line.

## Files

| file | change |
|---|---|
| `src/archivum/doi_audit/ingest_check.py` | new: `IngestDoiCheck`, `check_entry_doi`, `record_ingest_check` |
| `src/archivum/web/routes/ingest.py` | the shared commit helper; `doi_confirmed`; banner text |
| `src/archivum/web/templates/_ingest_preview.html` | the DOI warning and its confirm form |
| `src/archivum/web/templates/_ingest_workbench.html`, `_ingest_reference.html` | pass `doi_warning` through to the preview |
| `src/archivum/web/templates/help.html` | Ingest section |
| `tests/test_ingest_doi_check.py` | new, on `tmp_library` with a fake transport, as `tests/test_doi_audit_check.py` |
| `CHANGELOG.md`, `pyproject.toml`, `uv.lock` | the bump |
| `D:\Projects\Using\archivum\doi-citation.qmd` | usage section, rendered |

## Acceptance

Tests, none touching the network or the production library:

1. An entry whose DOI matches is imported with no extra step; `counts.jsonl` has its count
   under the allocated tag, `where cites > 0` selects it, and the banner has "Cited".
2. A mismatched DOI: the first commit writes nothing (`ref.feather` byte-identical), the
   response has the warning and the confirm button; the same commit with `doi_confirmed` equal
   to the DOI imports.
3. `doi_confirmed` for one DOI does not confirm a different, edited DOI.
4. An entry with no DOI sends no request and imports.
5. A transport that raises: the reference is imported and the banner says the DOI was not
   checked.
6. A duplicate is reported before any request is sent.
7. Both routes: document and reference-only.
8. The check sends only the DOI: no title, author or tag appears in any request.

By hand, on the running app: one real paper with its right DOI, one with a deliberately wrong
DOI, one Google Scholar paste with none.

## Decisions (Steve, 2026-11-18)

1. **An entry with no DOI**: do nothing. No DOI, no check, no count. Ingest's discovery has
   already asked Crossref for one.
2. **Sources unreachable**: import, and say "not checked" in the banner. It should not occur.
3. **Only a full match passes.** A weak match stops and asks. Read here as the `verified`
   class and nothing else, so `year differs` and `not compared` also stop. To be confirmed:
   "full" is the Audit page's rule, title similarity 0.9 or more after folding case, accents,
   LaTeX and punctuation, with the same first author; it is not similarity of exactly 1.
4. **Both commits**: a document with its entry, and a reference with no document.

## Execution notes

### 2.16.0a18 (2026-11-18)

- Built as planned, at Steve's "go ahead on ingest plan". The query-triggered count lookup he
  raised beside it was dropped at his word: the library's DOIs already have their counts.
- **Divergence: the duplicate test runs only for an entry with a `doi` field.** An entry with
  none goes straight to the import as before, with no extra importer pass, since there is
  nothing to send and the import reports a duplicate itself.
- **Divergence: a confirmed DOI's count is stored too**, as a Check DOIs run stores the count
  of a mismatch. The banner says "for that DOI".
- **`result_sentence` is in `ingest_check.py`**, with the check, so the text is tested without
  Flask.
- **Measured.** `derive_cites` on a copy of the real `counts.jsonl`, 2,940 rows: 22 ms. Two
  real checks at OpenAlex through `egress.default()`: 486 ms for the first request (Artzner
  et al., verified, title match 1.00, 10,722 citations) and 156 ms for the second (the junk
  DOI `10.1017/CBO9781107415324.004` on the same entry: mismatch, 0.33).
- **The bar is the Audit page's Verified class**, title similarity 0.9 or more. Steve was told
  of this reading and did not change it. One consequence seen in testing: "Coherent measures of
  risk II" scores 0.94 against "Coherent Measures of Risk" and would pass. Raising the bar for
  Ingest alone is a one-line change (`STRONG_TITLE` is shared with the Audit page).
- Tests: `tests/test_ingest_doi_check.py`, 24, on a temporary library with a fake transport.
  Gate: `uv run --no-sync pytest -q`, 438 passed, 26 skipped.
- Not done by me: the three checks by hand on the running app (a right DOI, a wrong one, a
  Google Scholar paste with none).
