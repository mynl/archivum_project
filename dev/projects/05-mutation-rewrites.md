# Project 05 — Mutation rewrites + change_log  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 04.

## One-line goal

Rewrite all 10 `save()`-call-sites as backend transactions, with a `change_log` row written in the same tx. Delete the watchdog (replaced by `PRAGMA data_version` based `is_stale()`). Drop `bibtex.bib` regen from the `record_read` hot path. Keep it tight for ref-mutating ops (the @new-ref differentiator).

## Decisions already locked

- 10 mutation sites with bibtex-regen treatment per the table in `dev/projects/README.md`.
- `change_log(id, ts, op, tbl, pk, before_json, after_json, source)` appended in the same tx as the mutation.
- Watchdog code deleted: `LibraryChangeHandler`, `start_watcher`, `stop_watcher`, `_ignore_until`, `_last_mtimes`.
- `Library.refresh_if_stale()` calls `backend.is_stale()` via `PRAGMA data_version`.
- Web `before_app_request` calls `refresh_if_stale()` instead of `check_for_reload`.
- Cross-process refresh: web ↔ uber shell yes, Jupyter no (caller manually calls `lib.reset()`).
- Bibtex regen **kept** for: `update_reference`, `remove_reference`, `update(importer)`, `enhance_refs`.
- Bibtex regen **dropped** for: `record_read`, `link_document`, `validate`, `update_hashes`, `enhance_doc_df`.

## ABC additions for this project

```python
def transaction(self) -> ContextManager: ...
def upsert_refs(self, rows, *, source: str) -> None: ...
def delete_ref(self, tag, *, source: str) -> None: ...
def upsert_docs(self, rows, *, source: str) -> None: ...
def update_doc_paths(self, updates, *, source: str) -> None: ...
def upsert_ref_doc(self, rows, *, source: str) -> None: ...
def delete_ref_doc(self, *, tag=None, hash_=None, version=None, source: str) -> None: ...
def bump_read(self, file_hash, caller, *, source: str) -> None: ...
def change_log_append(self, op, tbl, pk, before, after, source) -> None: ...
def is_stale(self) -> bool: ...
```

## Files to read first

- `src/archivum/library.py` — the 10 mutation methods. Specifically: `link_document` (~215), `record_read` (~395), `update` (~458), `remove_reference` (~525), `update_reference` (~533), `validate` (~576), `save` (~753), `update_hashes` (~1083), `enhance_refs` (~1649).
- `src/archivum/enhancements.py:1078` — the other `save()` call (`enhance_doc_df`).
- `src/archivum/web/routes/documents.py` lines 17, 38 — `record_read` callers.
- `src/archivum/web/routes/edit.py:64` — `update_reference` caller.
- `src/archivum/web/routes/shared.py:33-39` — the current `check_for_reload` hook to replace.
- `src/archivum/web/app.py` lines 18–25 — `before_request` and `inject_lib`.
- `src/archivum/cli.py:712` — CLI `library_save` (may become a no-op or VACUUM trigger).

## Open design questions (resolve when writing the brief)

- Per-row `change_log` for bulk inserts (an import inserts ~100 ref rows) vs single `op='batch'` summary row? Per-row gives granular rollback (the whole point); summary is cheaper. Probably per-row.
- `change_log` retention policy: meta plan said 90 days for `record_read`, all-forever for ref mutations. Confirm before implementing. Could be a one-liner CLI cron.
- Order: backend tx commit, then bibtex write outside the tx (it's a filesystem side-effect, not a row). What if bibtex write fails post-commit? Acceptable — DB is source of truth, bibtex is derived. Loud log.
- This project may split (5a non-ref + 5b ref+bibtex) if it grows beyond one focused session.

## Risks / gotchas

- `import_bibtex.update_library` has its own sharding + audit side effects; rewriting `Library.update(importer)` is the biggest single piece of work here.
- `enhance_refs` and `enhance_doc_df` are bulk replace-the-table operations — transactions must be all-or-nothing.
- Bibtex regen failure must not leave the DB inconsistent: write DB first, then bibtex; tolerate bibtex failure with a loud log but don't roll back the DB.
- `record_read` rewrite drops bibtex regen — confirm with the `/view` route end-to-end that nothing downstream relied on the side-effect.

## Likely drift sources (revisit this stub after)

- Whatever Project 04's ABC ends up looking like determines this project's method signatures.
- `change_log` schema may need a small tweak after the first end-to-end run.
