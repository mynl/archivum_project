# DOI audit and citation counts: the Audit page, its journal, and its commit path

Status: ruled 2026-11-17, executing. The first six steps run first, with a pause for the user's
real-library trials after [audit page and DOI normalization] and after [citation counts
collection]; the last four steps need a separate go-ahead. Written against 2.16.0a1. Versions are assigned
at execution: each step below is one commit and one pre-release bump, with its CHANGELOG entry,
its `help.html` change where the step is web-visible, and its tests in the same commit. Steps
land in the order written. The first six steps deliver DOI normalization and citation counts and
are useful alone; the last four deliver the audit proper and can wait.

**Size warning.** The whole plan is a large build: ten steps, one new package, one new page, and
roughly 2,500 to 3,500 lines including tests. Each step is small and separately revertible.

## Goal

Add an enrichment process to archivum that

1. normalizes, checks, and discovers DOIs for the references in a chosen subset of the library;
2. collects citation counts for the same subset from public sources, and keeps every count ever
   collected so growth can be tracked over time;
3. stores all of that work **apart from the master data**, shows it for review and editing, and
   writes to the master records only on an explicit Commit of ticked rows;
4. makes the counts available to querex, so `!wang where cites > 100` works on the Query page;
5. can run slowly in the background for hours without tripping a source's usage limits, and
   resumes after any interruption.

The subset is chosen with a Network-style query box (querex, ripgrep, or both). The page is
**More → Audit**, admin only.

## Measured starting point (default library, 2026-11-16, aggregates only)

| quantity | count |
|---|---:|
| references (all tags unique) | 7,440 |
| with a DOI | 3,037 |
| without a DOI | 4,403 |
| without a DOI, year 2000 or later | 3,280 |
| DOIs with an `http(s)://` prefix | 11 |
| DOIs with a `doi:` prefix | 2 |
| DOIs with trailing punctuation or embedded whitespace | 8 |
| DOIs not starting `10.` after prefix stripping | 12 |
| DOIs containing uppercase letters | 540 |
| rows sharing a DOI with another row | 129 |
| references with an `eprint` value | 765 |
| arXiv DOIs (`10.48550/...`) | 0 |

By type, the references without a DOI are 2,680 articles, 1,047 books, 406 technical reports,
126 misc, 53 unpublished, 46 incollection, 35 inproceedings, and 10 theses.

Consequences. Normalization touches about 20 rows, which makes it a small and safe first trial of
the commit path. Counts for the 3,037 DOI holders cost about 31 batched OpenAlex requests, a
matter of seconds. The slow job is title search for the 4,403 references without a DOI: at one
request per second in polite mode, about 75 minutes at Crossref, plus OpenAlex searches for the
Crossref misses (P4, R6). A large share of those searches will find nothing (books, reports, old proceedings),
and the negative results are stored so they are not repeated for months.

## Decisions settled with the user (2026-11-16)

- **D1 tags are fixed.** Nothing in this feature renames a tag. No exceptions.
- **D2 author and year of an existing reference are never written by Audit.** A disagreement
  with a source is shown as information, with a link to Edit, and has no commit path here.
- **D3 review everything.** While trust is being built, every change is ticked by hand. No
  bulk accept and nothing ticked in advance.
- **D4 normalization first.** Stripping `http://doi.org/` and similar is the first thing built
  and the first thing committed.
- **D5 the journal DOI wins** over an arXiv DOI: the journal version is the official one.
- **D6 published-version backfill** writes journal, volume, number, pages, and type together, as
  one proposal accepted or rejected whole. Entry type may change, except that a `book` is never
  changed to another type.
- **D7 polite-pool email** is `stephen.j.mildenhall@gmail.com`, held in the global config.
- **D8 the SQLite migration is permanently shelved.** Everything here is built on Feather and
  JSONL files. `dev/projects/10-citations-openalex.md` is superseded by this plan.

## Decisions proposed in the draft, ruled by the user 2026-11-17

P1, P2, P3 and P5 were agreed as written. P4 was rewritten after the user asked for a fast mode.

- **P1 a published version in a different year becomes a new reference**, which references the
  existing document. The user's idea. When
  `Wang2019` (arXiv) was published in a journal in 2021, Audit proposes a **new** reference for
  the journal version, with a tag from the importer's allocator (`Wang2021` or the next free
  suffix), linked to the same document. `Wang2019` is left untouched, so every existing citation
  still resolves, and new writing cites the journal version. When the journal year equals the
  preprint year, the proposal is an in-place backfill under D6, since the tag is already right
  and neither author nor year changes. Together with D2, these two cases mean Audit never writes
  an existing reference's tag, author, or year. Costs: the library gains a second reference for
  one work, both appear in query results, and both show the same citation count (OpenAlex
  usually merges preprint and journal version into one work).
- **P2 normalization does not change case.** DOIs are case-insensitive and 540 contain capitals,
  often the publisher's own form (`S0167-6687...`). All comparisons fold case; stored values
  keep theirs.
- **P3 sources.** OpenAlex is the primary source and defines the `cites` column. Crossref is the
  second source for DOI metadata and title search. `doi.org` is the test of whether a DOI
  exists, because Crossref returns 404 for DOIs registered elsewhere (DataCite, including
  arXiv). The arXiv API identifies published versions. Semantic Scholar is the last step and
  optional.
- **P4 pace: two modes, chosen on the page.** Speedy: up to 3 concurrent requests per host,
  capped at 2 per second for searches and 5 per second for single records. Polite: one request
  at a time, 1 per second. arXiv is 1 request per 3 seconds on one connection in both modes,
  which is its terms of use. The default is speedy when the run needs 500 requests or fewer
  and polite above that; the confirmation shows the request count and the mode, with a selector
  to override. Speedy is a 3-worker thread pool behind the per-host limiter in `egress`, not
  asyncio: the sources cap concurrency at 3, so an event loop and a second HTTP library would
  buy nothing. Published limits found 2026-11-17: OpenAlex 30 requests per second with a free
  key (10 without), a budget of $1 per day with a free key, 100 values per filter; Crossref
  polite pool 10 per second for single records and 3 per second for searches, concurrency 3.
- **P5 counts for references without a DOI** attach only through an OpenAlex work the user has
  accepted in review. A wrong match would give a wrong count with nothing to flag it. Most
  highly cited articles since about 2000 have a DOI; the gap is the 1,047 books and 406
  technical reports, where OpenAlex often has a work with no DOI.

## Rulings from the execution review (2026-11-17)

- **R1 tables.** The review and counts tables are rendered the way the existing archivum pages
  render tables. `csv_grid` is not a dependency of archivum; converting archivum to
  `greater_tables` and `csv_grid` is a separate project. This overrides "rendered server-side
  with `csv_grid.to_html`" below.
- **R2 names.** "Audit" already means the structural audit (`Library.audit()`,
  `library-audit`), `clean-audit`, and the `import-audit/` folder. This feature uses
  `doi-audit`: the folder `<library>/doi-audit/`, the package `archivum.doi_audit`, and the
  commands `doi-audit-run`, `doi-audit-status`, `doi-audit-revert`. The page stays **Audit**.
- **R3 entry-count check.** Before `write_bibtex` may refuse on an entry-count mismatch, the
  count is measured on the live library, read only, aggregate only. The result is in the
  execution notes.
- **R4 a failed replace.** `os.replace` fails on Windows while another program has
  `bibtex.bib` open. `write_bibtex` retries briefly, then raises, leaving the old `bibtex.bib`
  intact and the `.tmp` file beside it.
- **R5 `contact_email`** is added to the live `~\.archivum\global-config.yaml` at the
  [egress client] step, with the address in D7.
- **R6 OpenAlex budget.** The binding limit at OpenAlex is the daily dollar budget, not the
  rate. `egress` stops a run when the `X-RateLimit-Remaining` header runs low, which replaces
  the per-host daily request cap. [missing DOI search] searches Crossref first, which is free,
  and uses OpenAlex title search only for Crossref misses. The key is in the user environment
  variable `OPENALEX_API_KEY` (set by the user 2026-11-17). The per-request prices were not
  found; the first real run records `meta.cost_usd` in the execution notes.
- **R7 the `requests.` test** matches imports and calls of `requests` in `.py` files, since
  the literal text also occurs in a docstring and in the vendored `pdf.mjs`.

## Context for a cold reader

- **Read "Read this first" in `CLAUDE.md`.** The default library is the user's real archive.
  Every test that writes uses the `tmp_library` fixture in `tests/conftest.py`
  (`make_tmp_library`), as `tests/test_edit_save.py` does. No test calls a live source: the
  egress module takes an injected transport.
- **Master data** is `ref.feather` in `~\.archivum\libraries\<name>\`. `bibtex.bib` is a
  projection of it, rewritten whole by `Library.write_bibtex` at the end of every
  `Library.save`. The BibTeX fields written are those in the `ref_columns` whitelist.
- **`Library.save`** (`library.py`, near line 1267) already makes timestamped backups of the
  Feathers in `backups/` (last 10 kept), refuses to overwrite a populated file with an empty
  frame, and sets `_ignore_until` so the watcher ignores its own writes.
- **`Library.write_bibtex`** (near line 1566) copies the old file to a single `bibtex.bak` and
  then calls `write_text`, which truncates in place. A crash mid-write leaves a truncated file,
  and a second write overwrites the only backup. Step [bibtex write hardening] fixes both.
- **The watcher** (`LibraryChangeHandler`, near line 126) watches the library folder
  non-recursively and reacts only to `ref.feather`, `doc.feather`, and `ref-doc.feather`. Files
  in a subfolder cannot set `needs_reload`. The audit files go in a subfolder for that reason.
- **`Library.update_reference`** replaces a whole entry and renames the tag when the entry's tag
  differs. Audit must not use it. Audit gets its own narrow writer, `apply_field_changes`.
- **`Library.database`** (near line 658) is the merged frame behind the Query page and the
  universe resolver. It is cached in `_database` and rebuilt after `reset()`.
- **`search/universe.py`** resolves a combined query to document **hashes**. References with no
  document have no hash and would be dropped, so Audit needs a tag-level resolver (step [audit
  page and DOI normalization]).
- **Existing outbound code.** `crossref.py` calls `requests.get` directly, with the email
  hard-coded. `alex.py` imports `pyalex`, which is not a dependency, and nothing imports
  `alex.py`: it is dead. `routes/ingest.py` downloads a URL the user types, which cannot be
  allow-listed and stays as it is.
- **`TODO.md`** has no open item in this area. The `import-bibtex -vv` crash (commit `1a81148`)
  concerns the importer's verbose path, which step [published versions] does not use.

## Files and storage

Everything Audit collects is stored in `<library>/doi-audit/`, created on first use.

| file | content | written by |
|---|---|---|
| `observations.jsonl` | append-only; one line per source response used as audit evidence | runner |
| `counts.jsonl` | append-only; one slim line per count collected: time, tag, source, source id, count | runner |
| `decisions.jsonl` | append-only; one line per review decision (accepted, rejected, work accepted for counts) | Audit page |
| `changesets/<stamp>_<name>.json` | one file per Commit: every row with old value, new value, evidence, and outcome | commit path |
| `cites.feather` | derived from `counts.jsonl`; one row per tag | runner, at the end of a run |
| `run.lock`, `run-state.json` | single-flight lock (pid, start time) and progress of the active run | runner |

An observation line holds: `at` (ISO, UTC), `run_id`, `tag`, `source`, `query_kind` (`doi`,
`title`, `source_id`, `arxiv_id`), `query`, `status` (`ok`, `not_found`, `error`),
`http_status`, and for a hit `source_id`, `doi`, `title`, `year`, `first_author`, `container`,
`volume`, `issue`, `pages`, `type`, `cited_by_count`, `score`, plus up to three `candidates` for
a title search. JSONL is used because an append is a single write, a crash loses at most one
line, and the file is readable in any editor. Growth is about 7,440 slim lines per source per
refresh in `counts.jsonl`, roughly 1 MB per monthly refresh.

The rule that keeps the master data safe is structural: **the runner writes only inside
`doi-audit/`; only the commit path writes `ref.feather`.**

## New code

```
src/archivum/egress.py            the one outbound HTTP function: allow-list, pacing, backoff
src/archivum/doi_audit/__init__.py
src/archivum/doi_audit/doi.py         normalize, classify, match score; pure functions
src/archivum/doi_audit/sources.py     OpenAlex, Crossref, doi.org, arXiv; each returns plain records
src/archivum/doi_audit/journal.py     append and read the JSONL files; derive cites.feather
src/archivum/doi_audit/runner.py      work queue, single-flight, progress, stop
src/archivum/web/routes/audit.py
src/archivum/web/templates/audit.html  (+ components/audit_*.html)
tests/test_doi_audit_*.py
```

CLI commands, flat and hyphenated like the existing ones: `doi-audit-run`, `doi-audit-status`,
`doi-audit-revert`. Review and Commit are on the web page only.

## The commit path

`Library.apply_field_changes(changes, name) -> Path` is the only way Audit writes master data.
`changes` is a list of `(tag, field, old, new)`.

1. **Validate.** `field` must be one of `doi`, `type`, `journal`, `volume`, `number`, `pages`.
   The tag must exist. A change of `type` away from `book` is refused. `tag`, `author`, and
   `year` are refused by construction, not by convention.
2. **Reload if stale.** If `needs_reload` is set, reload from disk first, so a stale in-memory
   frame is never saved over newer data.
3. **Write the changeset file**, outcome `pending`, before anything else changes.
4. **Compare and swap.** A row applies only if the current value equals `old`. Otherwise it is
   marked `skipped: changed since review` and left alone, as previewed tags are advisory in
   Ingest.
5. **Apply to a copy, then check the postcondition.** The copy must have the same rows in the
   same order, the same tags, the same columns and dtypes, and differ from the original in
   exactly the applied cells. Any other difference raises, nothing is saved, and the changeset
   is marked `failed`.
6. **Save once** through `Library.save()`, for the whole batch.
7. **Verify on disk.** Re-read `ref.feather` and confirm the applied cells; confirm the entry
   count of `bibtex.bib` equals the reference count. Mark the changeset `applied`.

`archivum doi-audit-revert <changeset> -x` builds the inverse changeset and applies it through the
same seven steps, so the compare-and-swap also protects edits made after the commit. Without
`-x` it shows what would be reverted.

## DOI classes and the match score

`doi_audit/doi.py` holds the rules, with no I/O.

**Normalization** strips surrounding whitespace, a leading `https?://(dx.)?doi.org/`, a leading
`doi:`, URL-encoding, and trailing `.`, `,`, or `;`. Case is kept (P2). A value that does not
start `10.` afterwards gets no proposal and is shown for attention.

**Match score** between a reference and a source record: titles are normalized (LaTeX braces and
commands removed, Unicode folded, punctuation removed, lower case, whitespace collapsed) and
compared with a similarity ratio $t \in [0, 1]$ (`difflib.SequenceMatcher` unless `rustfuzz`
offers a pairwise ratio); first-author surnames are compared after folding; years agree when
they differ by at most 1, which allows for online-first publication.

| class | rule | action offered |
|---|---|---|
| `malformed` | normalization changes the value | commit the normalized DOI |
| `verified` | DOI exists; $t \ge 0.9$ and surname equal | none |
| `unresolved` | `doi.org` has no such DOI | edit the DOI by hand, or clear it |
| `mismatch` | DOI exists; $t < 0.6$ or surname differs | edit by hand; link to Edit |
| `shared` | the DOI is on more than one tag | link to Edit's merge; no commit here |
| `year differs` | DOI verified; years differ by more than 1 | information only (D2) |
| `proposed (exact)` | no DOI; titles equal, surname equal, years agree | commit the DOI |
| `proposed (strong)` | no DOI; $t \ge 0.9$, surname equal | commit the DOI |
| `ambiguous` | no DOI; best $0.6 \le t < 0.9$, or several candidates | choose a candidate or reject |
| `not found` | no DOI; nothing at $t \ge 0.6$ | none; not asked again for 180 days |
| `published version` | arXiv reference with a journal version | step [published versions] |

The thresholds 0.9 and 0.6 are provisional. The first full run produces the score distribution,
and the thresholds are set from it and recorded in the execution notes.

## Runner, pacing, and background cost

One engine, `doi_audit/runner.py`, started either by `archivum doi-audit-run "<query>" --counts --dois
-x` in a terminal (the way to run overnight) or by a button on the Audit page (a daemon thread
in the `serve` process, for subsets). Both take `doi-audit/run.lock`; a second start is refused while
the pid in the lock is alive.

- **Work queue** = the tags of the resolved subset, minus those with a fresh result. Fresh means
  a count newer than `doi_audit_refresh_days` (default 30) or a `not_found` title search newer than
  180 days. A run interrupted by a restart is resumed by starting it again: finished tags are
  skipped because their results are in the journal.
- **Order.** Batched DOI lookups first (100 per OpenAlex request), then single title searches.
- **Pace.** `egress` enforces, per host, the rate and the concurrency of the chosen mode (P4),
  honors `Retry-After`, and backs off on 429 and 5xx (30 s, doubling, capped at 1 hour).
  Five consecutive failures on a host stop that host for the rest of the run, and the run
  summary shows it. A low OpenAlex budget stops the run early (R6).
- **Stop** is a separate button (and Ctrl-C in the terminal). The runner finishes the request in
  flight and exits.
- **Refresh** is started by a person, from the page or the terminal, or by a Windows scheduled
  task the user sets up. There is no in-app timer.

**Background-cost lines** (required by the dev-tree rules):

| work | trigger | highest rate and its mechanism | cost of one run | writes | can a write reach its trigger |
|---|---|---|---|---|---|
| runner | a person: button or CLI | per host, at most 5 requests per second and 3 at once (speedy) or 1 per second and 1 at once (polite), enforced by the limiter and the semaphore in `egress`; one runner, enforced by `run.lock`; every request has a 30 s timeout | one HTTP request and one appended line | `doi-audit/*.jsonl`, `run-state.json` at most every 10 s, `cites.feather` once at run end | no: the trigger is a person, and the watcher ignores `doi-audit/` |
| progress display | Audit page open **and** a run active | one HTMX request per 5 s, stopped by the server response when no run is active | one read of `run-state.json` | none | no |
| counts reload | any web request | one `stat` of `cites.feather` per request, inside the existing before-request hook | one `stat`; a rebuild of `database` only when the mtime changed, which happens once per run | none | no |

The idle check before handover for steps that touch the runner: `serve --prod`, Audit page
closed, one minute left alone, CPU time and child processes of the `archivum` process sampled
with `Get-Process` at the start and end; then the same with a run active. Both measurements go
in the execution notes, and the command goes into `CLAUDE.md`.

## The Audit page

More → Audit, admin only, in the admin group beside Status and Health
(`('main.audit_page', 'Audit', 'bi-clipboard-check', true)` in `base.html`). The layout follows
the existing dense pages.

- **Query box**, as on Network, with the shared search history. An empty box means the whole
  library.
- **Summary strip** for the subset, computed locally with no network call: references, with and
  without a DOI, malformed, last checked, with counts, counts older than 30 days.
- **Actions**, each a separate, plainly labeled button: Normalize DOIs (local), Check DOIs, Find
  missing DOIs, Fetch citation counts, Stop run. Each network action first shows the number of
  requests and the estimated duration, and starts only on confirmation.
- **Review table**, rendered server-side with `csv_grid.to_html`, 50 rows per page, filtered by
  class: tick box, tag (link to Edit), class, field, current value, proposed value (editable),
  source, score, and the source's title, first author, and year beside the library's. Two
  actions: Commit ticked, and Reject ticked. A rejection is stored in `decisions.jsonl` and the
  proposal is not shown again.
- **Commit result**: applied, skipped, the changeset file, and the changeset list with a Revert
  action per changeset.
- **Counts table** for the subset: tag, title, cites, as-of date, growth over the last year.

## Steps

### [bibtex write hardening]

Change `Library.write_bibtex` to write `bibtex.bib.tmp` beside the target, flush and `fsync`,
check that the text is non-empty and its entry count equals the deduplicated reference count,
and then `os.replace` onto `bibtex.bib`. Replace the single `bibtex.bak` with dated copies in
`backups/` (`bibtex_<stamp>.bib`, last 10 kept, matching the Feathers). The symlink at
`bibtex_file` points at the path and survives the replace; confirm that in a test.

The Feathers are deliberately left as they are. An atomic replace raises a moved event, not a
modified event, and the watcher that tells the web process about writes from another process
listens only for modified. The Feathers already have timestamped backups and the wipe guard.

Acceptance: a test that makes the write fail part-way leaves the old `bibtex.bib` byte-identical;
a test that the entry-count check refuses a short file; existing BibTeX tests pass.

### [changeset commit]

Add `Library.apply_field_changes` as specified under "The commit path", and the `doi-audit-revert`
command. No web change.

Acceptance, all on `tmp_library`: a commit changes exactly the listed cells; tags, authors,
years, row count, and row order are identical before and after; a row whose value moved since
review is skipped and reported; a forced postcondition failure saves nothing; a change to `tag`,
`author`, or `year` is refused; a `book` type change is refused; revert restores the original
bytes of every changed cell; a changeset file exists for every outcome.

### [audit page and DOI normalization]

Add the page, the menu entry, the query box, and the summary strip. Add
`resolve_universe_tags(lib, raw_query)` to `search/universe.py`: the querex part selects tags
from `lib.database`, including references with no document; a ripgrep part restricts to tags
whose primary document matched. Add `doi_audit/doi.py` with normalization, the Normalize DOIs
action, the review table, Commit ticked, Reject ticked, and the changeset list with Revert.
There are no network calls in this step. Update `help.html`.

Acceptance: unit table for normalization (each prefix form, trailing punctuation, encoded slash,
case kept, a non-`10.` value produces no proposal); a route test that a non-admin gets 403; a
tag-level universe test including a reference with no document; on the real library, the user
reviews and commits the roughly 20 malformed DOIs and reverts one as a trial.

### [egress client]

Add `egress.py`: one `get`/`post` function with a host allow-list (`api.openalex.org`,
`api.crossref.org`, `doi.org`, `export.arxiv.org`; `api.semanticscholar.org` added in its own
step), per-host minimum interval, timeout, `Retry-After`, backoff, the failure breaker, the
daily cap, and an injectable transport for tests. Add `contact_email` to `Configurator` and the
global config, and build the `User-Agent` and `mailto` from it. API keys come from the
environment (`OPENALEX_API_KEY`), never from a file in the repo. Route the three functions in
`crossref.py` through `egress` with unchanged signatures. Delete `alex.py`. Update the module
map in `CLAUDE.md`.

Before writing the OpenAlex client, read the current OpenAlex and Crossref usage terms (key
requirement, daily limits, batch size for the DOI filter) and record them in the execution
notes; the defaults under P4 are set from memory and may be out of date.

Acceptance: a test that a host outside the list is refused; a test that `requests.` appears in
`src/archivum` only in `egress.py` and `web/routes/ingest.py`; pacing and backoff tests with a
fake clock; the `crossref` CLI command still works.

### [citation counts collection]

Add `doi_audit/sources.py` (OpenAlex works by DOI in batches of 50, and by work id), `doi_audit/journal.py`,
`doi_audit/runner.py`, the `doi-audit-run` and `doi-audit-status` commands, and the Fetch citation counts
action with its estimate, progress display, and Stop run. Counts are collected for references
with a DOI; a DOI that OpenAlex does not have is stored as `not_found`. Derive `cites.feather` at
the end of a run. Update `help.html`.

`cites.feather` columns: `tag`, `cites` (latest OpenAlex count), `cites_asof`, `cites_1y`
(increase over the trailing 365 days, empty until a year of history exists), `openalex_id`.

Acceptance: with a fake transport, a run over a `tmp_library` appends the expected lines; a
second run makes no requests (everything fresh); a run killed part-way resumes without repeating
finished tags; a second runner is refused by the lock; a write in `doi-audit/` does not set
`needs_reload`; the idle check, with numbers. On the real library, the user runs the 3,037 DOI
holders and inspects the counts table.

### [citation counts in query]

Left-join `cites.feather` into `Library.database` on `tag`, adding `cites`, `cites_asof`, and
`cites_1y`. The join is in `database`, never in `ref_df`, so counts cannot reach `bibtex.bib`.
Add `cites.feather` to `_library_signature` in `universe.py`, so cached universes are dropped
when counts change, and add the mtime check to the before-request hook. Integers display with
commas; a reference with no count shows blank. Update `help.html` with the new fields.

Acceptance: `!wang where cites > 100` returns rows on a `tmp_library` with counts and none
without; a reference with no count is excluded by `cites > 0` and causes no error; `bibtex.bib`
is byte-identical before and after the join; the build time of `database` is measured before and
after on the real library and both numbers are recorded.

### [DOI check]

Add the Check DOIs action: for each DOI in the subset, test existence at `doi.org`, fetch the
record from OpenAlex (reusing the count run's response when fresh) and Crossref, score it, and
class it `verified`, `unresolved`, `mismatch`, `shared`, or `year differs`. The review table
offers a hand-edited replacement DOI or clearing the DOI, both through the commit path. Update
`help.html`.

Acceptance: classification unit tests from stored fake responses, including a DataCite DOI that
Crossref does not have and that must not be classed `unresolved`; the score distribution from the
first real run recorded, and the thresholds confirmed or changed.

### [missing DOI search]

Add the Find missing DOIs action: title search at OpenAlex and Crossref for references with no
DOI, candidates scored and classed `proposed (exact)`, `proposed (strong)`, `ambiguous`, or `not
found`. An accepted proposal commits the DOI; accepting also stores the OpenAlex work id as
accepted for counts (P5). Where both an arXiv DOI and a journal DOI are found, the journal DOI
is proposed (D5). Update `help.html`.

Acceptance: fake-response tests for each class; a rejected proposal does not reappear; a `not
found` result is not searched again within 180 days; on the real library, the overnight run
finishes with no host stopped by the breaker, and the run summary is recorded.

### [published versions]

For references with an `eprint` or an arXiv URL, ask the arXiv API (batched id list, one request
per 3 seconds) for the record's own `doi` and `journal_ref`, and check Crossref's
`is-preprint-of` relation. A published version is certain when the arXiv record itself gives the
journal DOI and the match score passes; anything weaker is shown as possible and needs a
hand-entered DOI.

- **Same year:** propose the in-place backfill of `doi`, `type`, `journal`, `volume`, `number`,
  and `pages` as one unit (D6), through the commit path.
- **Different year (P1):** propose a new reference. The BibTeX is built from the library's own
  author and title text (keeping the user's name forms) and the journal's year, venue, and DOI,
  shown in an editable box as in Ingest, previewed with
  `Library.preview_staged_document_import` and imported with `Library.import_staged_document`
  (no staged file, `allow_duplicate=True`, since the title duplicates the preprint by design).
  The new tag comes from the importer. The preprint's primary document is then linked to the new
  reference with the existing link function. The pairing is stored in `decisions.jsonl`. The
  preprint reference is not modified.

Update `help.html`.

Acceptance: after a same-year backfill the tag, author, and year are unchanged; after a
different-year import the old reference is byte-identical, the new tag did not previously exist,
and both tags open the same document; a `book` is never offered a type change.

### [Semantic Scholar counts] (optional, last)

Add `api.semanticscholar.org` to the allow-list, a batch client (key from
`SEMANTIC_SCHOLAR_API_KEY`), and a second count per tag in `counts.jsonl`. `cites` stays the
OpenAlex figure; the second count appears in the counts table as its own column. Skip this step
if the OpenAlex counts prove sufficient.

## Deferred

- A "tick all exact matches shown" control, once the matching is trusted (D3).
- A citation count on the Query result line, and a history chart per reference.
- A pointer field between a preprint and its published version in `ref.feather`; the pairing is
  in `decisions.jsonl` until a use for it appears.
- Routing the Ingest URL download through `egress`.

## Execution notes

### 2.16.0a2 [bibtex write hardening] (2026-11-17)

- **R3 measurement**, default library, read only: 7,440 rows, 7,440 after duplicate tags are
  dropped, 7,440 entries in the built text by three ways of counting, and the file on disk
  equal to the rebuilt text. The check is safe to enforce today.
- **Divergence: how entries are counted.** The old count, `txt.count("\n@") + 1`, takes an `@`
  that starts a line inside a field for an entry. `count_bibtex_entries` in `bibtex.py` counts a
  header only at the start of the text or after a blank line.
- **Divergence: an empty library** still writes an empty `bibtex.bib`. The plan's "text is
  non-empty" is covered by the count check: zero entries pass only for zero references.
- **Known edge.** A reference with a tag and no other whitelisted field builds no entry, so the
  count check refuses the BibTeX write for the whole library until that reference is given a
  field. The live library has none.
- **R4** is 5 attempts, 0.25 s apart (`BIBTEX_REPLACE_ATTEMPTS`, `BIBTEX_REPLACE_WAIT_SECONDS`).
- An existing `bibtex.bak` in a library folder is left in place; nothing writes or reads it now.
- Tests: `tests/test_bibtex_write.py` (8). Gate: `uv run --extra test pytest -q`, 159 passed,
  26 skipped.

### 2.16.0a3 [changeset commit] (2026-11-17)

- **Measured on the live library** (read only): the six writable columns are all `str` dtype
  and hold both missing values and empty strings (`doi`: 253 missing, 4,150 empty). So a
  changeset records a missing value as `null` and an empty string as `""`, the compare and swap
  tells them apart, and a revert restores whichever was there.
- **Divergence: a new module**, `doi_audit/changesets.py`, holds the rules and the changeset
  files; `Library.apply_field_changes` is the write. The plan's file list did not have it.
- **Divergence: a refused batch writes no changeset.** Validation raises `ValueError` for the
  whole batch before anything is written, so "a changeset file exists for every outcome" means
  the outcomes `applied`, `skipped` (no row applied) and `failed`.
- **Divergence: further refusals** beyond the plan's list: a field that is not a column of
  `ref.feather`, a tag on more than one reference, the same cell twice, a row that changes
  nothing, and an entry type BibTeX does not accept.
- **Divergence: a revert may restore a book's type.** A change from `article` to `book` is
  allowed, and its inverse would otherwise be refused. `reverts=` on `apply_field_changes`
  lifts the book rule and nothing else.
- `doi-audit-revert` with no argument lists the changesets.
- The `qd` table printer fails on a column of empty strings (the open [import-bibtex-vv] item
  in `TODO.md`), so the revert command shows `(missing)`, `(empty)` and `-`.
- Tests: `tests/test_doi_audit_changesets.py` (24). Gate: `.venv\Scripts\python -m pytest -q`,
  183 passed, 26 skipped. `uv run` could not be used: the running `archivum serve` holds
  `archivum.exe`, and `uv` wants to rebuild it for the new version.

### 2.16.0a4 [audit page and DOI normalization] (2026-11-17)

- **Live library, read only, counts only:** 12 malformed, 18 needing attention, 131 sharing a
  DOI (the draft counted 129 by exact text; the page compares normalized, without case).
- **Divergence: "attention" is wider than the draft's "does not start `10.`".** It is any value
  that is not `10.prefix/suffix` after normalization, so a value with no suffix or with
  whitespace inside is listed too. It has its own button, Show DOIs needing attention, and no
  tick boxes.
- **Divergence: normalization repeats until stable** and also strips `www.doi.org`, so
  `doi: https://doi.org/10.1/x.` is reduced in one pass.
- **Divergence: two more modules**, `doi_audit/journal.py` (brought forward from [citation
  counts collection] for `decisions.jsonl`) and `doi_audit/proposals.py` (the summary and the
  review rows, kept out of the route).
- **Divergence: the summary strip** has the six local counts. "Last checked", "with counts" and
  "counts older than 30 days" arrive with the steps that produce them.
- **R1:** the tables are plain Bootstrap tables in `components/audit_body.html`.
- The Audit box shares the Network history (box `network`) and selects on Enter.
- `routes/audit.py` imports by name, not `from .shared import *`.
- `resolve_universe_tags` shares `_query_frame` with `_query_hashes`, which was split for it.
  Checked on the live library: the hash universe is unchanged in size (7,223 for the empty
  query), and the tag universe has all 7,440.
- Browser check on a temp library (headless Chromium, 1400 px and 390 px): select, Normalize,
  tick, Commit, change subset, Revert; no console errors, no horizontal overflow at 390 px.
- Tests: `tests/test_doi_audit_page.py` (47). Gate: `.venv\Scripts\python -m pytest -q`, 230
  passed, 26 skipped. The slow web tests were not run: they can write to the production
  `semantic-embeddings.feather`.
- **Left for the user:** review and commit the 12 malformed DOIs on the real library, and
  revert one as a trial.

### 2.16.0a5 [egress client] (2026-11-17)

- **Source terms, measured with six real requests for public works** (through `egress`, polite):

  | source | request | cost | notes |
  |---|---|---|---|
  | OpenAlex | works filtered by a DOI list, one page | $0.0001 (1 credit) | up to 100 DOIs per filter |
  | OpenAlex | title search, one page | $0.001 (10 credits) | |
  | OpenAlex | one work by DOI | free | |
  | Crossref | title search | free | headers: 3 per second, concurrency 3 |
  | doi.org | HEAD | free | 302 for a DOI that exists, 404 for one that does not |

  The free OpenAlex key has 10,000 credits ($1) a day; `X-RateLimit-Limit` and
  `X-RateLimit-Remaining` are in credits. Counts for the 3,037 DOI holders cost 31 credits.
  Title search for the 4,403 without a DOI would cost about $4.40 at OpenAlex, so R6
  (Crossref first) stands.
- **Divergence: no redirect is followed.** `doi.org` answers an existing DOI with a redirect to
  the publisher, a host outside the list. The transport returns the redirect as the answer.
- **Divergence: two ways of failing.** A run retries with backoff. The Crossref lookups used by
  ingest and the CLI pass `retry=False`: one attempt, 10 s timeout, nothing on failure, as
  before. A 30 s backoff inside an ingest would have been a regression.
- **Divergence: the budget stop** is a fraction (under 2% of the day's limit left), read from
  the two headers, so it does not depend on the unit.
- **Divergence: `EgressFailed`** wraps a transport failure for a `retry=False` caller, so
  `crossref.py` needs nothing from `requests`.
- **R5 done:** `contact_email` appended to the live `global-config.yaml`. A process running
  older code ignores an unknown key with a warning.
- **Finding, not changed:** `arxiv.py` reaches arXiv through the `arxiv` package, outside
  `egress`. [published versions] will ask arXiv through `egress`; moving `arxiv.py` is a
  separate item.
- No background work in this step, so no idle check: `egress` runs only when called.
- Tests: `tests/test_egress.py` (36), none sending a request. Gate:
  `uv run --extra test pytest -q`, 266 passed, 26 skipped. `archivum crossref -d
  10.1111/1467-9965.00068` returns the entry.

### 2.16.0a6 [citation counts collection] (2026-11-17)

- **Idle check, with numbers.**

  | measurement | result |
  |---|---|
  | `serve --prod`, production library, Audit page visited once then closed, no run, 60 s (`scripts\Measure-ArchivumIdle.ps1`, twice) | 0.000 CPU s; no child processes; 0 writes |
  | polite run of 60 requests (6,000 references, 0.3 s simulated latency) with the page polling every 5 s, 60 s, temp library, in-process harness | 0.09 CPU s, 0.2% of one core, about 2 ms CPU per request of 100 DOIs |
  | one status poll after a run has ended, which also redraws the summary strip for 6,000 references | 36 ms CPU; it happens once per run |

  The run-active figure is from a harness in one Python process with the real runner, real
  pacing and a fake transport, not from a release `serve` with a real run, because a real run
  on the production library is the user's trial. `Measure-ArchivumIdle.ps1` says how to take it
  during that trial.
- **Divergence: Stop works across processes** through a `run.stop` file the runner looks for
  before each request, so the page can stop a run started in a terminal. A run in the same
  process is also told directly, which ends a wait at once.
- **Divergence: progress of a run in the same process comes from memory**, so the status line
  is current at each 5 s poll; `run-state.json` is still written at most every 10 s, and is
  what another process reads.
- **Divergence: the poll that reports the end of a run also redraws the summary strip** (an
  out-of-band swap), and leaves the rest of the page alone since a review may be in progress.
- **Divergence: references sharing a DOI are asked about once** and each gets the count. A DOI
  containing `,` or `|`, which are separators in an OpenAlex filter, is asked about singly.
- **Divergence: freshness is keyed on tag and DOI**, so a reference whose DOI has changed
  (normalized, corrected) is asked about again. A not-found is fresh for the same 30 days. An
  error is not a result, and is asked about again.
- **Divergence: `psutil` became a direct dependency** (already installed, through another
  package) to test whether the process in `run.lock` is alive. `os.kill(pid, 0)` is not safe
  on Windows, where signal 0 is Ctrl-C.
- **Divergence: `doi_audit_refresh_days`** is a config field (default 30), as the draft's
  `audit_refresh_days`.
- `cites.feather` columns: `tag`, `cites` (int64), `cites_asof` (ISO date text), `cites_1y`
  (nullable Int64), `openalex_id`.
- The estimate shown is max(pacing, latency / concurrency) with 0.5 s assumed latency.
- **Note for the real run:** the server must be started from a shell that has
  `OPENALEX_API_KEY`; a variable set at User level after a shell opened is not in that shell.
  The confirmation panel says so in red when the key is missing.
- Browser check on a temp library with a fake OpenAlex (headless Chromium): plan, choose
  polite, Start run, status line polling to the end, summary redrawn, counts table; no console
  errors.
- Tests: `tests/test_doi_audit_counts.py` (26). Gate: `.venv\Scripts\python -m pytest -q`, 292
  passed, 26 skipped (`uv run` again blocked by the running server holding `archivum.exe`).
- **Left for the user:** run the 3,037 DOI holders on the real library and inspect the counts
  table.
