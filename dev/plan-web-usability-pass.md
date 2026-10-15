# Web usability pass: result line, menu, date added, Edit, ref-only, delete/merge, reader

Status: final draft, 2026-10-15, awaiting go-ahead. Current version 2.5.1. Versions are
assigned at execution: each step below is one commit and one bump ([repo-hygiene],
[export-paths], and [edit-keys] patches, the rest minor), with its CHANGELOG entry, its `help.html` change, and its `TODO.md` update in the same
commit. Steps land in the order written. **[reader] is last and self-contained**, so
`git revert` of that one commit (plus a patch bump) restores the old viewer if the user does not
like it.

## Goal

The web app is in daily use and works well. Eight irritations, from the user:

1. Query results: show **date added** and **read count** after the hash on the dense line.
2. Menu: **Query, Ingest, Ripgrep, Edit** at top level; everything else under **More**, demoted
   pages first; **About** at the bottom of More, reporting versions of the app and major tools.
3. Edit: Up/Down in the BibTeX textarea **jumps to the next tag** instead of moving the caret.
4. Query: **Read** becomes a split button — Read (a blend of recency and count), Read by count,
   Read by recent.
5. Add a **reference with no document** (rare, but needed), and be able to **search for** such
   references.
6. Open documents in a viewer with a **single-page view**, as a browser tab beside the control
   window. The current built-in browser viewer (Adobe-powered) has no single-page mode.
7. Query: a **Mine** button after Read — the user's own publications, newest first.
8. Edit: **delete** and **merge** references, and **change the document** attached to a
   reference.
9. (Added mid-review.) File paths in BibTeX exports do not show where the documents are stored
   on DOOB. And nothing should write into the checkout's `temp/`, which held real data that
   reached git.

`TODO.md` items 1 (surface the version) and 2 (Edit arrow keys) are folded in: [menu-about] and
[edit-keys]. Move them to Done with the version they ship in.

## Decisions (settled 2026-10-15)

- **D1 viewer.** In-app PDF.js reader, [reader]. Done **last, in its own commit**, for easy
  rollback. No Sumatra integration.
- **D2 Help** goes in More: this is a single-user app. The dev-tree rule "Help is a `?` button
  top right opening a right-side offcanvas panel" (`V:\dev\CLAUDE.md`) is overridden here, so
  [menu-about] adds that override to the **Deltas** section of `archivum/CLAUDE.md`.
- **D3 read blend half-life** 30 days, in the config file as `read_half_life_days`.
- **D4 Mine pattern** `owner_author_pattern: "Mildenhall, Stephen"`. Plain `Mildenhall` would
  also catch the user's children.
- **D5 zero reads** are not shown on the result line (only `×N` for N ≥ 1).
- **D6 date added.** References get their own `added` timestamp, [added]. Backfill: the earliest
  `create` among a reference's documents; references with no document get **2026-01-01**, the
  stand-in date for the original Mendeley import. Recent sorts by `added`. A `docs` count makes
  ref-only references searchable.
- **D7 export paths** point at the real store, `D:\archivum\ShardedDocLibrary\<shard>\<name>`,
  not the app-home symlink. The main `bibtex.bib` does not change: it has no `file` field and
  keeps none.
- **D8 `temp/`.** `temp` in the checkout is now the user's own scratch link, to
  `D:\Projects\Hacks\archivum` (backed up). All of it leaves git; code never writes there. The
  repo is public and the files are on `origin/master`; git history is **not** rewritten (see
  [repo-hygiene]).

## Context for a cold reader

- **Read this first in `CLAUDE.md`.** Tests run against the user's production library. Any test
  that mutates library state must use an in-memory fake library — the `_fake_library(tmp_path)`
  pattern in `tests/test_web_ingest_import.py` with `LibraryContext.get` monkeypatched — or
  snapshot and restore the file it writes, as `tests/test_read_tracking.py` does for
  `read.feather`. No test may call `save()` on the live library.
- **Data.** `ref.feather` (refs by `tag`), `doc.feather` (files by `hash`, `version`),
  `ref-doc.feather` (links, with `priority`; 0 is the primary that `/view/<tag>` opens),
  `read.feather` (`hash, last_read, read_count, last_caller`; written by `Library.record_read`,
  `library.py:507`, on `/view`, `/view-hash`, `/text`).
- **`Library.database`** (`library.py:553`) right-joins refs to their primary link and doc row,
  then left-joins read history; `read_count` is filled with 0. One row per tag; refs with no
  document appear with a blank hash.
- **Dates today.** The only date is the document's `create`: the source file's creation
  timestamp captured at import (`import_bibtex.py:369`, `library.py:1619`), stored tz-aware in
  the configured timezone. It is querex's `recent_field`
  (`configurations/querexfuzz-database-config.yaml`), so Recent means "recently acquired
  documents". `ref.feather` has no timestamp: the importer stamps `arc-source` with the import
  time (`import_bibtex.py:322`), but `Library.update` keeps only `config.ref_columns`, which
  omits it. The production library (`uber-library`) has no `import-audit/` trail either; it was
  built by the full-store loader. [added] fixes this.
- **querex** results carry only `base_cols` unless the query selects otherwise. querex sorts
  *before* it selects columns (`.venv/Lib/site-packages/querexfuzz/engine.py`, steps 5–7), so
  `order by` may name a column that is not selected. `order by -a, -b` takes several keys
  (`grammar.lark:53`).
- **Authors** are stored BibTeX-style, `Last, First Middle and ...`; the importer expands
  initials to the longest known form through a Trie (`import_bibtex.py:658`).
- **BibTeX projection.** `Library.write_bibtex` whitelists `ref_columns`; `dict_to_bibtex`
  without a whitelist (the Edit textarea) drops `arc-*`, `mendeley-*`, and `merge_count`
  (`bibtex.py:113`).
- **Admin.** `web/app.py` sets `g.is_admin` from `request.remote_addr` (anything but the bridge
  `10.8.0.1`). Do not change that rule.

## Steps

### [repo-hygiene] Nothing writes into the checkout; `temp/` leaves git (patch)

**Current.**

- `temp` in the checkout is now a symlink to `D:\Projects\Hacks\archivum`, the user's backed-up
  scratch area. The existing `.gitignore` rule `/temp` (no trailing slash) already matches the
  link itself — `/temp/` would not, because git treats a symlink as a file — so git ignores it.
- **Data in git.** 24 files committed under `temp/` before that rule existed are still in the
  index and in `HEAD`, and are on `origin/master` of a **public** repo. `git status` shows them
  as deleted, since git no longer looks through the link. They include full-library BibTeX dumps
  (`library01.bib`, `library01 - Copy.bib`, `library-wrong-file-format.bib`, about 4.7 MB each),
  the two web exports (with document paths), `books.bib`, notebooks with outputs, scratch
  scripts, and a staged PDF (`temp/staging/f1.pdf`).
- Four code paths write into `temp/` relative to the working directory — into the checkout when
  serving from it, and now through the link onto D:
  - `web/services/exports.py:85`, `_temp_export_path`: every CSV and BibTeX export;
  - `web/services/ingest_batch.py:29`, `STAGING_ROOT = Path("temp/staging")`: staged ingest
    documents;
  - `web/routes/qmd.py:21`: `temp/qmd_extract.qmd`;
  - `pyproject.toml:111`: pytest `cache_dir = "temp/pytest-cache"`.
- `.gitignore` has three stale `temp/...` lines (61, 66, 69) made redundant by `/temp`, and
  `pytest_cache/` (line 40) does not match pytest's default `.pytest_cache/`.
- `CLAUDE.md` line 28 holds a BEL control character where `D:\archivum` should be (an unescaped
  `\a`). Its Commands and Layout sections say the pytest cache lives in `temp/pytest-cache/` and
  that `temp/` is disposable.

**Change.**

- `git rm -r --cached temp`: this records the deletions; the files themselves are safe on D:.
  History is **not** rewritten. The repo is public, so assume the old commits are already copied.
  Purging would mean `git filter-repo` plus a force push of every branch and tag (rewriting every
  later SHA, including the `Version-*` tags), a GitHub support request to drop cached commits,
  and it would do nothing about forks or scrapers. The content — bibliographic metadata,
  local paths, one published PDF — does not justify that.
- Exports: build the CSV and BibTeX in memory and send them with
  `send_file(io.BytesIO(...), as_attachment=True, download_name=..., mimetype=...)`; delete
  `_temp_export_path`.
- Staging: `STAGING_ROOT = BASE_DIR / "staging"`. That is the app home: derived, disposable
  state, created lazily, which the dev-tree rule says is where the app writes ("never into the
  repo or the dev tree"). Batch cleanup is unchanged. The ingest tests that isolate staging with
  `monkeypatch.chdir(tmp_path)` (`tests/test_ingest_batch.py:84`,
  `tests/test_web_ingest_import.py:243` and following) switch to monkeypatching
  `ingest_batch.STAGING_ROOT`.
- QMD extraction: write the text into a `tempfile.TemporaryDirectory()` (system temp) for
  `QmdParser`, removed on exit.
- pytest: drop the `cache_dir` override, so the cache is `.pytest_cache/` in the checkout: build
  churn on V:, gitignored.
- `.gitignore`: keep `/temp`, with a comment saying it is the user's scratch link and must keep
  no trailing slash; delete lines 61, 66, 69; add `.pytest_cache/`.
- `CLAUDE.md`: fix the line-28 store path; the pytest cache is `.pytest_cache/`; `temp/` is the
  user's scratch link, and code never writes there.

**Acceptance.** `rg -n '"temp' src pyproject.toml` finds no writes into `temp`. `git ls-files
temp` is empty. Ingest tests pass with staging under a monkeypatched root. Route tests: CSV and
BibTeX exports return attachments and create no file in the working directory. Manual: one web
ingest (staging appears under `~/.archivum/staging` and is cleaned up) and one export.

### [export-paths] BibTeX+ paths point at the real store (patch)

**Current.** The BibTeX+ export (the Export button's BibTeX+ option on Query, Ripgrep, Network;
`plus=1`) writes `file = {:C\:/Users/steve/.archivum/docs/<shard>/<name>.pdf:pdf}`.
`web/services/exports.py:27` resolves paths with `lib.abspath`, which joins the relative doc path
(every `doc.feather` row is `<shard>/<name>`) onto `doc_store_path`, the app-home symlink
`~/.archivum/docs`. The files live at `D:\archivum\ShardedDocLibrary\<shard>\<name>`. The symlink
path works only on DOOB while the app home exists, embeds the user-profile path, and is not where
the files are. The main `bibtex.bib` has no `file` field and is out of scope (D7).
`document.py:723` writes a staging path into a transient review bib; leave it.

**Change.** `Library.doc_store_real_path`: `doc_store_path.resolve()`, computed once (cached).
The BibTeX+ resolver joins relative doc paths onto it and passes absolute paths through — one
resolve per export, not one per file. Result:
`:D\:/archivum/ShardedDocLibrary/<shard>/<name>.pdf:pdf`.

**Acceptance.** Unit test: `rows_to_bibtex` with the new resolver, on a fake library whose
`doc_store_real_path` is monkeypatched to another root, writes that root into `file`. Manual: the
`file` paths in a BibTeX+ export exist on disk under `D:\archivum\ShardedDocLibrary`.

### [edit-keys] Arrow keys stay inside the BibTeX textarea (patch)

**Current.** `edit.html:107` installs a `window` keydown listener that turns every ArrowUp /
ArrowDown into `navigateList(±1)` unless focus is `#tag-filter`. In the `#bibtex` textarea
(`components/edit_form.html`) the arrow loads the adjacent tag and silently discards unsaved
edits.

**Change.**

- Return early when `e.target.closest('input, textarea, select, [contenteditable]')`. The filter
  box keeps its own keyup handler (`filterTags`), unchanged.
- `authors.html:93` has the same global listener. Apply the same guard, but first check whether
  its filter input relies on the global listener for navigation; if so, exempt that input by id,
  as `edit.html` does.
- Unsaved-change guard: mark the form dirty on `input` in `#bibtex`; before a sidebar click or
  arrow navigation loads another tag, `confirm('Discard unsaved changes to <tag>?')`. Clear the
  flag on save and cancel.

**Acceptance.** Playwright test in `tests/web/test_browser_smoke.py`: open `/edit?tag=<a tag>`,
focus `#bibtex`, press ArrowDown twice; the editor still shows the same tag and
`selectionStart` has moved. Manually: arrows outside text fields still walk the list.

### [menu-about] Menu order, More, About, version (minor)

**Current.** `base.html` navbar: Query, Ripgrep, Reports, Authors, Network, Qmd, Ingest\*,
Edit\*, Help, More (Cloud, Insights, divider, Status\*, Health\*). \*Admin only. The version is
surfaced nowhere.

**Change.**

- Top level: Query, Ingest\*, Ripgrep, Edit\*, More.
- More: Reports, Authors, Network, Qmd, Help — divider — Cloud, Insights — divider — Status\*,
  Health\* — divider — About.
- The More toggle shows the active style when the current endpoint is one of its items (define
  that endpoint list once in the template).
- `archivum/CLAUDE.md` **Deltas**: add the override of the dev-tree Help rule (D2), quoting it,
  with the reason: single-user app; Help is a More item and a full page.
- **About** at `/about` (route in `routes/status.py`, template `about.html`), open to all — it
  is read-only information. Collect everything in a new Flask-free module
  `src/archivum/versions.py`:
  - archivum `__version__`; when running from a git checkout, `git rev-parse --short HEAD` plus
    a dirty marker. Python version.
  - Python packages via `importlib.metadata.version`, one list constant: flask, waitress,
    pandas, numpy, pyarrow, querexfuzz, skimmatch, uber-shell, pymupdf, pypdf, pybtex,
    sentence-transformers, torch, umap-learn, hdbscan, networkx, matplotlib, watchdog.
  - Front end: Bootstrap, Bootstrap Icons, HTMX. **Single-source them**: move the three version
    strings out of the CDN URLs in `base.html` into one dict (for example `FRONTEND_VERSIONS` in
    `web/app.py`), exposed by the context processor; `base.html` builds its URLs from it, so
    About cannot disagree with what the page loads. ([reader] adds PDF.js to this dict.)
  - External tools: rg, pandoc, quarto, tectonic, pdftotext, the configured `ddjvu_command`.
    Subprocess with a 5 s timeout, first non-empty line of stdout or stderr (pdftotext and ddjvu
    print to stderr), "not found" when absent. Cache per process (`functools.cache`) so About is
    instant after the first load.
- CLI: `@click.version_option(__version__, prog_name="archivum")` on the `entry` group
  (`cli.py:375`).
- TODO 1 worried that `__version__` lags a pyproject bump until `uv sync`. `uv run` rebuilds
  the editable install when `pyproject.toml` changes (uv's default cache keys), so
  `uv run archivum ...` should report the new version. Verify once with the bump in this step;
  if it lags, stop and raise it rather than reading `pyproject.toml` at runtime.

**Acceptance.** Unit test: `versions.py` returns a mapping containing `archivum` equal to
`__version__`, and a missing executable yields "not found" rather than raising. Route test:
`GET /about` is 200 and shows the version. Manual: menu order, More highlighting on a demoted
page, hamburger collapse at phone width, `uv run archivum --version`.

### [added] A date-added timestamp on every reference; ref-only search (minor)

**Current.** See "Dates today" in Context. Recent sorts by the document's `create`, so ref-only
references never appear under Recent, and replacing a reference's document moves it up Recent.
No query can ask for references without a document: the merged view has no document count.

**Change: the column.**

- `ref.feather` gains `added`: when the reference entered the library, tz-aware in the
  configured timezone like `create`.
- **Structural**, not a config choice: kept whatever `ref_columns` says. Add it to the
  always-kept set beside `tag` and `type` in `Library.update_reference` (`library.py:674`) and to
  the column filter in `Library.update` (`library.py:596`). Audit every other path that writes
  `ref.feather` (`definitive_loader.py`, `initial_import`, `save`) for column filters that would
  drop it.
- **Never exported or hand-edited.** `write_bibtex` whitelists `ref_columns`, so `bibtex.bib`
  already excludes it. Add `added` to the fallback exclusions in `dict_to_bibtex`
  (`bibtex.py:113`) so it stays out of the Edit textarea; the Edit header shows it read-only.
- **Stamped in one place.** `Library.save()` sets `added = now` on every row where it is NaT —
  but only when the column exists, so a library that has not been backfilled is not
  mass-stamped. Every write goes through `save()`, so every new reference (Ingest, CLI
  `import-bibtex`, ref-only, `update_reference` with a new tag) is stamped with no per-path
  code. `save()` also normalizes the column to tz-aware datetime, since concatenating a Series
  row can degrade it to object.
- **Preserved.** `Library.update` replaces re-imported tags (`drop_duplicates(keep='last')`);
  carry the earlier `added` across. `update_reference` copies the existing row, so edits and tag
  renames keep it. A merge keeps the earlier of the two ([edit-delete-merge]).

**Change: the backfill**, once per library.

- `Library.backfill_added(undated, execute=False)` and CLI
  `archivum backfill-added --undated YYYY-MM-DD [-x]`. It fills only missing values and never
  overwrites:
  - a reference with documents gets the earliest `create` across **all** its linked documents
    (every priority — the earliest acquisition), so Recent's order barely moves;
  - a reference with no documents, or whose documents have no `create`, gets `--undated`, run
    as `--undated 2026-01-01` (D6: the stand-in for the Mendeley import date), as midnight in
    the configured timezone.
- The dry run prints counts per rule and the resulting date range. `-x` writes through `save()`,
  which makes its usual feather backup. On the production library the implementer runs the dry
  run, shows the user, and runs `-x` only on the user's word.

**Change: query and display.**

- `Library.database` adds `docs`, the number of documents linked to the tag (every priority; 0
  for ref-only). `docs > 1` finds references with alternates.
- When `ref_df` has no `added` column (a library not yet backfilled), `Library.database` sets
  `added = create`, so the shared querex config works for every library.
- `querexfuzz-database-config.yaml`: `recent_field: added`; add `added` to `date_fields` and to
  `base_cols` (keep `create`). Recent — the button, the `recent top 50 ...` wrapping of
  querex-symbol input, and anything else using `recent` — now means "recently added
  references".
- The Recent dropdown on the Query page gains a divider and **Reference only**:
  `q select added, * where docs == 0 order by -added` (if querex rejects `==`, use `docs < 1`).
- `help.html`: what Recent means now, `added` and `docs` as query fields, Reference only.
- CHANGELOG notes the behavior change: replacing a reference's document no longer moves it up
  Recent.

**Acceptance.** Fake-library unit tests: `save()` stamps NaT rows only when the column exists;
`update()` keeps the earlier `added` for a re-imported tag; `update_reference` with a new tag
stamps it; `dict_to_bibtex` omits it; backfill rules (minimum `create` across links, the
`--undated` fallback, existing values untouched, dry run writes nothing); `docs` counts; the
`added = create` fallback. Route test: the Reference-only query returns 200 with no query-error
feedback. Manual, after the user's `-x`: Recent's order looks as before; Reference only lists
the expected entries.

### [result-line] Date added and read count on the dense result line (minor)

**Current.** The dense list in `components/results.html` (lines 79–96) ends with the 8-character
hash, shown only when there is one. `read_count` is not a querex base column, and a query with
an explicit `select` may drop the date too, so the result frame cannot be relied on to carry
either.

**Change.**

- `render_search_results` (`routes/shared.py`) passes a tag-indexed activity frame
  `lib.database[['tag', 'added', 'read_count']]` to `prepare_search_results`
  (`presenters/search.py`), which stays Flask-free and adds `added_display` (`YYYY-MM-DD`, blank
  for NaT) and `reads_display` (`×N`, blank for 0 per D5). The join is by tag, so it works
  whatever the query selected. Author search shares the same path and gets the fields for free.
- Dense line: the hash, or a muted `no doc` when there is none, then
  `<span class="doc-added">…</span> <span class="doc-reads">…</span>`, styled like `.doc-hash`
  (muted, monospace) in `style.css`.
- Table and verbose views are unchanged.

**Acceptance.** Unit test of `prepare_search_results` on a synthetic frame: tz-aware `added`,
NaT, `read_count` 0 and 5, a query frame that lacks `added`. Manual: Recent shows dates in
descending order, matching its sort; Reference only shows `no doc` lines.

### [query-buttons] Read split button with three orders; Mine (minor)

**Current.** The Read button (`query.html:43`, `executeReadHistory` at `query.html:104`) runs
`q top 50 select last_read, read_count, * where read_count > 0 order by -last_read`.

**Change: Read.** A split button in the same pattern as Recent (`query.html:31`). The main
button runs the blend; the dropdown lists Read (blend), Read by count
(`order by -read_count, -last_read`), and Read by recent (`order by -last_read`). Same
`top 50 select last_read, read_count, * where read_count > 0` prefix.

The blend. Let $n$ be the read count, $a$ the days since the last read, and $h$ a half-life in
days. Score $s = n\,2^{-a/h}$: each read's weight halves every $h$ days. We store only the count
and the last time, so this treats all $n$ reads as happening at the last read — an upper bound on
the exact decayed count $\sum_i 2^{-a_i/h}$. With $t$ the last-read time in days since a fixed
epoch, $\log_2 s = \log_2 n + t/h - \text{now}/h$; ordering by

$$\text{read\_rank} = \log_2 n + t/h$$

is therefore equivalent and does not depend on "now", so it can be computed once when
`Library.database` is built and cached without going stale. In words: one doubling of reads is
worth $h$ days of recency. With $h = 30$ (D3), a paper read 8 times and last opened 90 days ago
ties with one read once today; one read 4 times last week beats one read once yesterday.

- New `Configurator` field `read_half_life_days: float = Field(30.0, gt=0, ...)` (`config.py`,
  global section), so it can be changed in `global-config.yaml` or overridden per library.
- `Library.database` adds `read_rank` after the read merge, using
  `self.config.read_half_life_days` (NaN when unread). It need not be selected — querex sorts
  first.

**Change: Mine.**

- New `Configurator` field `owner_author_pattern: str = ""` (global section): a regex matched
  against `author`. `Configurator` is `extra="forbid"`, so the fields must exist before the keys
  go into `~/.archivum/global-config.yaml`. Set it to `"Mildenhall, Stephen"` (D4), quoted for
  the comma.
- Verify the pattern before relying on it: compare the hit count of `! /Mildenhall/` with
  `! /Mildenhall, Stephen/` and have the user look over the difference, in case some of their
  own entries are stored as initials (`Mildenhall, S. J.`) that the Trie did not expand. Widen
  the pattern in the config if so; no code change.
- Button **Mine** after Read: `q select year, * ! /<pattern>/ order by -year, -added` — the bang
  field is `author`; no `top`, so all of them. Inject the pattern with `|tojson`. When the
  pattern is empty the button is disabled with a tooltip naming the config key.

**Acceptance.** Unit test on a fake library with a synthetic `read_df`: `read_rank` orders the
cases above as stated, is NaN for unread rows, and responds to `read_half_life_days`. Route
tests: `/search?q=<each of the four queries>` returns 200 with no query-error feedback (GET only,
so the production library is safe). Manual: Mine lists the user's papers newest first, without
the children's.

### [ref-only] Add a reference with no document (minor)

**Current.** The model already allows it: `Library.database` right-joins refs, so a ref with no
links shows with a blank hash. The importer knows about file-less entries (`raw_no_file`,
`import_bibtex.py:746`) and has an `Import (Fill)` action for when a later document matches a
document-less ref by DOI or title (`import_analysis`, `import_bibtex.py:1147`). But web Ingest
needs a staged file: `Library._make_staged_document_importer` (`library.py:1811`) raises without
one, and `ingest_start` (`routes/ingest.py:237`) builds a file batch.

**Change.**

- **First, verify the CLI path**, which is the reference the web mirrors: a `.bib` holding one
  entry with no `file` field, `archivum import-bibtex x.bib -x`. Expect the ref added with no
  links and `added` stamped. If the importer fails with zero documents, fix the importer — do not
  bypass it (tag allocation, name normalization, and duplicate checks live there). If that fix
  proves large, stop and raise it.
- `Library`: let the staged preview and import pair take `staged_document_path=None` (no `file`
  field injected), or add `preview_reference_import` / `import_reference` beside them. Build the
  importer from a temporary review directory holding only the `.bib` (`doc_dir` must exist and
  contain no documents). Blocking rules unchanged: a DOI or title duplicate blocks, with a link
  to the existing tag.
- Ingest Zone B gets a separate, explicit button: **Add reference only (no document)**. It opens
  the workbench in a no-document mode: the document pane reads "Reference only"; Preview and
  Commit go through the importer; no replace or attach actions. Previewed tags are advisory, as
  usual.
- The new reference is stamped by `save()` ([added]), so it heads Recent and appears under
  Reference only. Attach a document later through [edit-documents], or let Ingest's
  `Import (Fill)` match it.

**Acceptance.** Unit test with the `FakeImporter` pattern: no `file` field injected,
`update_library` called, no text extraction. One real-importer test on a fake library in
`tmp_path`: a file-less entry ports with an allocated tag, an empty `ref_doc_df`, and `added`
set. Manual: add one; it tops Recent and Reference only; open it in Edit.

### [edit-documents] Documents panel on Edit (minor)

**Current.** `Library` has `link_document`, `unlink_document`, `promote_document`
(`library.py:234–320`) and `replace_document` (`library.py:1648`). The CLI exposes
`link-tag-hash`, `unlink-tag-hash`, `link-doc` (`cli.py:2477–2571`). The web exposes only
replace, and only from Ingest's Merge/Warn conflict (`/ingest/replace`, `routes/ingest.py:327`).
Edit shows only BibTeX.

**Change.**

- Below the BibTeX form, a **Documents** card: partial `components/edit_documents.html` served by
  `GET /edit-tag/<tag>/documents`. One row per link, joined to `doc_df` and `read_df`: priority
  (a Primary badge on 0), hash (8 characters), version, name, suffix, size, **file created**
  (the document's `create`, labeled so to keep it distinct from the reference's `added`), reads.
  Per row: **View** (a `/view-hash/<h>` link, so it works before and after [reader]), **Make
  primary** (`promote_document`), **Unlink** (`unlink_document`). When an unlink would leave the
  document linked to no reference, the confirm says it becomes an orphan: the file and its doc
  row stay, and `archivum validate orphans` lists it.
- **Attach file…** goes to `/ingest?attach_to=<tag>`. Ingest stages the upload or URL as now,
  and the workbench offers **Make primary document of `<tag>`**, which posts to the existing
  `/ingest/replace` with `match_tag=<tag>`. `replace_document` demotes the old file and never
  deletes it. Works for ref-only targets, which have no incumbent.
- **Link existing document**: a hash-prefix input (at least 6 characters; it must match exactly
  one hash in `doc_df`; reuse the hash completion) → `link_document(tag, hash, version,
  primary=False)`. Covers a book and its chapters sharing one file, and re-linking after an
  unlink.
- All mutating routes are POST and `@admin_required`; each re-renders the panel.

**Acceptance.** Unit tests of the panel's data assembly on a fake library. Route tests with
`LibraryContext.get` monkeypatched to a fake library for promote, unlink, and link; no
production writes. Manual: attach a file to a ref-only entry; it leaves Reference only.

### [edit-delete-merge] Delete and merge references from Edit (minor) — the largest step

**Delete.**

- **Current.** `Library.remove_reference` (`library.py:666`) drops the ref and its links, then
  saves; CLI `delete-tag` (`cli.py:711`) wraps it. There is no web path, and nothing records
  what was deleted beyond `save()`'s timestamped feather backups.
- `remove_reference` first writes `<library>/edit-audit/<timestamp>_delete_<tag>.bib`: the entry
  (`dict_to_bibtex`) plus `% added <iso>` and `% link <hash> <version> <priority>` comment
  lines, so it can be re-imported and re-linked. The CLI gains this too.
- Edit gets **Delete `<tag>`…** (outline-danger), which loads a confirm panel by HTMX: title,
  number of linked documents, what happens (ref and links removed; `bibtex.bib` rebuilt;
  documents, doc rows, and read history stay; the audit path), the citation warning below, and a
  separate **Delete `<tag>` permanently** button posting to `/edit-tag/<tag>/delete`. On success
  the editor shows the audit path and the sidebar item is removed.

**Merge.** For duplicate references to one work. On the Edit page of KEEP:

- **Merge another reference into `<keep>`…** opens a tag input with `/tag-suggest`
  autocomplete, then a preview panel:
  - the proposed merged BibTeX in an editable textarea: KEEP's non-empty fields win, ABSORB
    fills KEEP's empty fields, the tag is fixed to KEEP (rename separately with Save);
  - a conflicts table: fields where both are non-empty and differ after normalizing whitespace
    and braces, showing both values; the user settles them by editing the textarea;
  - the document plan: KEEP's links keep their order, ABSORB's are appended after them, and a
    duplicate `(hash, version)` is dropped;
  - the citation warning (below);
  - **Swap roles** (re-renders the preview with ABSORB as the survivor) and **Merge `<absorb>`
    into `<keep>`**, two separate buttons.
- `Library.merge_references(keep, absorb, merged_fields)` does a single save: update KEEP's
  fields; set KEEP's `added` to the earlier of the two; repoint ABSORB's ref-doc rows to KEEP,
  ranked after KEEP's (`assign_ref_doc_priority`); drop ABSORB; write
  `edit-audit/<timestamp>_merge_<absorb>_into_<keep>.bib` holding both originals. Read history
  is keyed by hash, so it follows the documents.
- A pure helper `propose_merge(keep_row, absorb_row) -> (merged, conflicts)` in `bibtex.py`.
- Reject `absorb == keep`, unknown tags, and a tag change in the merged text.

**Citation warning** (delete and merge). The removed tag leaves `bibtex.bib` and the linked copy
configured by `bibtex_file`, so any manuscript citing it — including those under `D:\projects`
— stops resolving. The panels say so plainly. There is no alias mechanism to fall back on:
biblatex's `ids` field is not honored by pandoc citeproc.

**Acceptance.** `propose_merge` unit tests: fill, conflict, normalization. `merge_references`
and `remove_reference` on a fake library in `tmp_path`: links repointed, priorities `0..n-1`,
KEEP's primary unchanged, duplicate link dropped, earlier `added` kept, audit file contents,
exactly one save. Route tests against a fake library. No test touches the production feathers.

### [reader] Documents open in an in-app PDF.js tab, single-page (minor) — LAST, own commit

**Rollback contract.** Everything this step needs lives in this one commit: the vendored viewer,
the `/file` routes, the `/view` split, `reader.html`, the PDF.js entry in `FRONTEND_VERSIONS`,
and its `help.html` and CHANGELOG text. No earlier step depends on it. To back out:
`git revert <sha>`, then a patch bump.

**Current.** Every document link points at `/view/<tag>` (`routes/documents.py:36`) with
`target="_blank"`: `results.html`, the `rg_*.html` components, `network.html:794` and `:819`,
and the Ingest banners. It `send_file`s the PDF to the browser's built-in viewer (Adobe-powered
in the user's browser), which has no non-continuous single-page mode. `/view` records a read.

**Change.**

- Vendor the Mozilla **PDF.js generic viewer** (the prebuilt `pdfjs-<ver>-dist.zip` from the
  GitHub releases, latest stable, pinned) into `src/archivum/web/static/vendor/pdfjs/` (`build/`,
  `web/`). Prune `web/locale` to en-US; keep `cmaps` and `standard_fonts`. Add a `VERSION` file;
  add PDF.js to `FRONTEND_VERSIONS` from it, so About reports it. Committed to the repo: no build
  step and no runtime CDN. **Cost: several MB of third-party files in the repo** — measure and
  report the size in the CHANGELOG entry.
- New raw routes `/file/<tag>` and `/file-hash/<h>`: `send_file(..., conditional=True)` so HTTP
  Range works (PDF.js fetches large files in chunks); **no read recorded** here, otherwise every
  range request would count as a read.
- `/view/<tag>` and `/view-hash/<h>`: when the document's suffix is `.pdf`, record the read once
  and render `reader.html`; otherwise `send_file` as today (djvu, epub).
- `reader.html`: a standalone full-viewport page with no navbar; `<title>` is the tag plus a
  short title, so tabs read as tags rather than shard filenames; the favicon set; one iframe on
  `static/vendor/pdfjs/web/viewer.html?file=<url_for('main.file', tag=tag)>`. The generic viewer
  dispatches `webviewerloaded` on `parent.document` before it initializes — the supported hook
  for embedding pages. The handler calls
  `e.detail.source.PDFViewerApplicationOptions.set(...)` with `scrollModeOnLoad = 3` (page
  scrolling: one page at a time), `spreadModeOnLoad = 0` (no spreads), and
  `defaultZoomValue = 'page-fit'`. Check the event and option names against the pinned release;
  **do not patch vendored files**. Focus the iframe on load so PgDn and the arrows page at once.
- The viewer keeps its own toolbar: find, outline, thumbnails, print, download.
- `help.html:93`: `/view` opens the reader for PDFs; `/file` serves raw bytes and does not count
  as a read; `/text` is unchanged.

**Acceptance.** Route tests: `/view/<pdf tag>` returns HTML whose iframe points at `/file/`;
`/file/<tag>` returns `application/pdf` and answers a `Range` request with 206; `/file` leaves
`read_count` unchanged (snapshot and restore `read.feather` as in `test_read_tracking.py`).
Browser smoke test: the reader loads and `PDFViewerApplication.pdfViewer.scrollMode === 3`.

## Out of scope

- Exact recency-weighted read scores. That needs per-read timestamps (an event log) or an
  exponentially weighted count column updated at each read,
  $S \leftarrow S\,2^{-\Delta/h} + 1$.
- Opening documents in Sumatra from the web app (D1).
- Help as an offcanvas panel (D2).

## Execution log

Executed from 2026-10-15, starting at 2.5.1. One step, one bump, one commit.

Review-time divergences:

- [repo-hygiene] The `git rm -r --cached temp` bullet was already done by the user in 5a73cb6 ("delete old temp files -> d: hacking"); `git ls-files temp` was empty at review.
- [query-buttons] `Configurator.save` writes every field that differs from, or is missing in, the raw `global-config.yaml` into the library `config.yaml`. A new defaulted field absent from the global file would therefore be frozen into `uber-library/config.yaml` at the first save and shadow later global edits. Both new keys (`read_half_life_days`, `owner_author_pattern`) go into `~/.archivum/global-config.yaml`, not only the latter.
- [ref-only] The CLI check `archivum import-bibtex x.bib -x` would write a junk entry into the production library. It is run instead through the real importer against a `tmp_path` library in the test suite.
- [added] The backfill dry run is shown to the user; `-x` waits for the user's word. Later steps rely on the `added = create` fallback in `Library.database` and do not depend on it.
- Browser tests at baseline: all 15 errored with "playwright install" (the cached Chromium did not match the venv's Playwright); `python -m playwright install chromium` was run once, as `CLAUDE.md` documents.

[repo-hygiene] 2.5.2:

- `CLAUDE.md` Layout also said plans are `dev/plan-<version>-<desc>.md`, contradicting the house rule (no version in the name); corrected to `dev/plan-<desc>.md` while editing those lines.
- `CLAUDE.md` Deltas carried a copy of the version ("Currently 2.5.1"), which would go stale at every bump of this plan; replaced by a pointer to `pyproject.toml`, the single source.
- Added `test_exports_are_built_in_memory` (CSV and BibTeX under a `chdir` to an empty `tmp_path`; the directory stays empty). The QMD route was smoke-checked the same way (no test added; the plan asks for none).
- The ingest tests' stray prefetch-thread warning (`FileNotFoundError` on `temp\staging\...\manifest.json`) disappeared with the `chdir` removal.

[export-paths] 2.5.3:

- The resolver is a public `Library.store_path(p)` beside `abspath` and `textpath` (same shape, different root), rather than a private helper in `exports.py`, so the CLI can reuse it. No name collision.
- Manual check run on the production library: a 25-row BibTeX+ export, all 25 `file` paths exist, all under `D:\archivum\ShardedDocLibrary`.
- Every bump also carries `uv.lock`, whose `archivum` version line `uv run` rewrites; 2.5.2 was amended (unpushed) to include it.
- Finding, outside this plan: the 6 `test_network_browser_data_reaches_graph_smoke` cases fail at baseline (2.5.1 code). `network.html:230` declares `let currentData` at the top level of a classic script, which never becomes `window.currentData`, so the test's `wait_for_function` times out after 180 s each. Not fixed here. Browser gates in this plan run with `-k "not network_browser_data"`; the other 9 browser tests passed at baseline.

[edit-keys] 2.5.4:

- `authors.html`'s filter input does rely on the global listener for navigation (its keyup handler only filters), so it is exempted by id as the plan anticipated.
- The guard is one `confirmDiscard()` called from `navigateList` (before the index moves) and from the sidebar button's inline `onclick`, which runs before htmx's listener and stops it with `stopImmediatePropagation` on refusal. The flag clears on any swap into `#editor-container` (htmx does not swap 4xx/5xx, so a failed save keeps it) and in a new `cancelEdit()`.
- The Playwright test also exercises the guard: Playwright answers the confirm (dismiss, then accept). `sample_tag` is the first tag in the list, so the accepted step reloads that same tag; the test checks the edit was discarded rather than the tag changing.

[menu-about] 2.6.0:

- `ddjvu` has no version flag (`--version` is rejected; no argument prints "Cannot decode document"). It is probed with `--help`, and any tool whose first output line carries no digit reports "found (no version reported)".
- The git revision ignores untracked files for the dirty marker (`status --porcelain --untracked-files=no`), and is cached per process like the tools, since it describes the code the process loaded.
- The More menu is one Jinja list of groups `(endpoint, label, icon, admin_only)`; it renders the items, the dividers (skipping the admin group for guests), the active item, and the toggle's active style. Demoted pages gained icons in the dropdown.
- Version lag (TODO 1): after the bump to 2.6.0, `uv run archivum --version` printed 2.6.0 at once, so nothing reads `pyproject.toml` at runtime.
- Menu checked with Playwright screenshots at 1400 px (More open; More active on Help and About) and 390 px (hamburger collapsed and open). The route test also pins the order and the More highlight.

[added] 2.7.0:

- One class constant, `Library.structural_ref_columns = ["tag", "type", "added"]`, drives the always-kept set in both `update` and `update_reference`, so the two cannot drift.
- `update_reference` also drops an `added` key from the parsed BibTeX, so a hand-typed `added = {...}` in the textarea cannot overwrite the date.
- Audit of other `ref.feather` writers: `definitive_loader.py` writes only through `Library` methods; `initial_import` goes through `update`; `Library.enhance_refs` (the one-time initial-import enhancement, `_ref_df = ans.ans_df`) replaces the frame wholesale and would drop `added` if ever rerun on a backfilled library. Left as is: it is a from-scratch build step; noted here.
- Tests use a new `make_tmp_library` / `tmp_library` factory in `tests/conftest.py`: a real `Library` over feathers in `tmp_path` with a real `Configurator` and no BibTeX link, so `save()` runs for real without touching production. Later steps reuse it.
- The `--undated` fallback for a library in `Europe/London` lands at `2026-01-01 00:00+00:00` (GMT in January), midnight local as specified.
- Backfill dry run on `uber-library` (CLI, nothing written): 7,195 references from documents (earliest 2019-05-28, latest 2026-10-15), 168 undated at 2026-01-01; no existing values. `-x` awaits the user's word. `Library.database` today: 168 references with `docs == 0`, 5 with `docs == 2`.
