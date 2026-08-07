# Changelog

Notable changes to Archivum. Versions follow `project.version` in `pyproject.toml`.

This file starts at 2.3.0. For earlier history see the `Version-*` git tags and
the session notes in `codex.md` and `GEMINI.md`.

## 2.4.0 (2026-08-07)

### Added

- **`/text/<tag>`** serves the extracted text of the tag's primary document,
  resolved through `Library.textpath` (the `full_text_lib` tree mirrors the
  document store's shard layout, with the extractor suffix). Served as
  `text/plain; charset=utf-8` so it renders inline rather than downloading, with
  an `inline; filename=<tag>.txt` disposition. 404 when the tag is unknown, has
  no document, or has no extracted text. Like `/view/<tag>`, it records a read.

## 2.3.0 (2026-08-06)

Query-line document hashes, batch ingest, duplicate replacement, and an end to
the audit-file litter.

### Data Model

- **`ref-doc.feather` gains a `priority` column.** `0` is the tag's primary
  document, `1`, `2`, ... are ordered alternates. Promote a document by zeroing
  its priority and demoting the incumbent.

  This is deliberately not `version`. `version` identifies *which sharded copy*
  of a content hash a row points at — one physical file linked from several
  references is sharded under several canonical names — and `ref-doc` joins
  `doc` on `(hash, version)`. Reusing it for ordering would repoint rows at the
  wrong file.

  No migration step is needed. `Library.ref_doc_df` backfills a missing column
  from current row order via `utilities.assign_ref_doc_priority`, which is what
  the old `.iloc[0]` callers already resolved to. Row identity is still
  `(tag, hash, version)`.

### Added

- **Query page**: each dense-list entry ends with the first 8 characters of the
  document hash, in small upper-case monospace, after the journal. It is the
  hash of the reference's primary document, so it matches what `View` opens and
  lines up with ripgrep results and document-store filenames. Omitted when a
  query's explicit `select` clause drops the `hash` column.
- **Batch ingest**: select or drop several files at once, or point the path box
  at a folder to queue every document in it (that folder only, not
  subdirectories). Documents are handled one at a time in the existing
  workbench, with `Importing M of N` and a progress bar. Remaining documents are
  analysed on a background thread while you work on the current one.
  `Skip` abandons one document, `Cancel batch` abandons the queue; both discard
  only staged copies and never touch the originals.
- **Ingest stays on the page**: outcomes accumulate as colored banners below the
  workbench instead of replacing it, and a summary appears when the queue
  drains. A single document is a batch of N=1, so the one-file flow behaves the
  same way.
- **Duplicate replacement**: when a staged file is new but its DOI or title
  matches a reference that already has a document (analysis action
  `Merge/Warn`), the preview now names that reference with a link and offers
  **Replace document on \<tag\>**, with an optional *also update the reference
  metadata from this BibTeX* checkbox. Previously this was a hard block with no
  way forward.
- Wider ingest format support: PDF, DjVu, EPUB, MOBI, CHM, XPS, FB2. Only PDFs
  preview inline and get text extraction; for the others metadata discovery
  usually finds nothing and you paste the BibTeX, which now degrades gracefully
  instead of failing the queue.
- `Library.replace_document`, `unlink_document`, `promote_document`,
  `register_document`, `primary_doc`, and `import_conflict`.
- CLI: `archivum clean-audit [--days N] -x` prunes accumulated audit and staging
  scratch from `debug_dir/imports`, `<library>/import-audit`, and
  `<library>/staging`. Dry run by default.
- CLI: `archivum unlink-tag-hash TAG HASH [-v N] -x` removes a ref-doc link
  without touching the document, and `archivum link-tag-hash --primary` makes a
  document the one a tag opens — promoting an existing link rather than skipping
  it. Together these undo a web replacement.

### Changed

- **`Library.database` joins only priority-0 ref-doc rows**, so a reference
  appears once and the hash shown in search results is the file `View` opens.
  Three tags previously listed twice. One document hash leaves the searchable
  universe as a result (an alternate copy on `Svindland2010a`); it remains
  reachable through `ref_doc_df` and `primary_doc`.
- **One primary-document resolver.** `Library.primary_doc(tag)` replaces the
  four call sites that disagreed: the view routes took the first ref-doc row
  while the query and cache paths took the highest version, which gave different
  answers for any tag with more than one document. Hash-to-metadata lookups now
  prefer the tag a document is primary for.
- `/view/<tag>` and `/view-hash/<h>` send a mimetype guessed from the suffix
  instead of always claiming `application/pdf`.
- `Library.get_tag_info` lists documents primary-first and labels the primary.
- The enhancement pipeline's "Best File" sort ranking is now applied as
  `priority` instead of being computed and discarded.
- Ingest form state moved from a raw `temp_path` to a validated
  `batch_id` + `idx` pair.
- The web ingest preview reports a failed import analysis as an instruction to
  paste a complete BibTeX entry, rather than surfacing a raw pandas error.

### Fixed

- **Web ingest no longer writes audit files.** A commit used to scatter seven
  files into `debug_dir/imports/<timestamp>/`, hard link copies of them into
  `<library>/import-audit/<timestamp>/`, and leave a review `.bib` in
  `<library>/staging/` on every preview *and* every commit — none of it ever
  cleaned. Bulk CLI `import-bibtex` still audits, because `Library.history()`
  reads that trail; staged single-document imports do not. Use `clean-audit` to
  prune the backlog.
- `Bib2df_Incremental._audit_dir_path` no longer creates a directory and hard
  links the input BibTeX as a side effect of being read, and `update_library`
  no longer touches it when `write_audit` is false.
- `debug_dir` is created on demand by its writers instead of on every `Library`
  construction.
- **Staged imports no longer rescan the whole staging directory.**
  `Bib2df_Incremental` receives `doc_dir=<staged>.parent` and scans and hashes
  everything it finds there; that was the shared `temp/staging` with dozens of
  stale files, on every preview and every commit. Each document is now staged in
  its own directory.
- `/view-temp` resolves through the batch manifest rather than a caller-supplied
  filename, closing a path-traversal hole, and is admin-gated.
- Review BibTeX filenames no longer collide between concurrent imports.
- Manifest updates are serialised, so the prefetch thread cannot clobber a
  status the user has just set, and prefetch no longer repeats the metadata
  lookup the request thread is already doing for the first document.

### Known Issues

- `tests/web/test_browser_smoke.py::test_network_browser_data_reaches_graph_smoke`
  (6 parametrizations) waits on `window.currentData`, but
  `templates/network.html` declares `let currentData`, which never becomes a
  `window` property. The tests cannot pass as written. Pre-existing; previously
  masked because they errored during Playwright setup.
- `Library.validate(task="missing")` and `document.elaborate_duplicates`
  reference a `ref_doc_df.path` column that does not exist and raise
  `AttributeError` when reached. Pre-existing.
