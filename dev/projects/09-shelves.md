# Project 09 — Shelves  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 07 (or 08).

## One-line goal

Activate the `shelf` + `shelf_member` tables (schema already in `archivum.db` from Project 03). Add a web page for managing shelves (sidebar of shelves + main pane of members), CLI commands, and CSV/BibTeX export of a shelf.

## Decisions already locked

- Tag-keyed members (`shelf_member.tag` references `ref.tag` with CASCADE on delete).
- Many-to-many: a tag can be in multiple shelves.
- Ordered: `position` column.
- `shelf(id, name UNIQUE, description, created_at, updated_at)`.

## Files to read first

- `dev/projects/README.md` — schema sketch for `shelf` / `shelf_member`.
- `src/archivum/web/routes/edit.py` and `templates/edit.html` — UI pattern to mirror.
- `src/archivum/web/services/exports.py` — CSV/BibTeX export pattern.

## Open design questions (resolve when writing the brief)

- Web UI: drag-to-reorder, or simpler list-with-up/down buttons? Defer to mockup at execution.
- CLI surface: `archivum shelf-create <name>`, `shelf-add <name> <tag>`, `shelf-list`, `shelf-remove`, `shelf-export <name>`. Confirm naming.
- "This paper's shelves" badge on query results and tag-info view — probably yes; small UX touch.
- Auto-generated shelves (e.g. "papers cited in report X") — out of scope here; flag for future.
- Initial seed: should we backfill any existing concept-of-shelves from the user's `mendeley-tags` field? Probably not automatic; suggest manually via `shelf-create + shelf-add` loop.

## Risks / gotchas

- Drag-reorder needs careful `position` updates (transactional). Avoid float-position fractional inserts; use periodic renumber if `position` gets sparse.
- Deleting a ref CASCADEs the `shelf_member` row. UI should reflect this gracefully (notify which shelves changed).

## Likely drift sources (revisit this stub after)

- Web UI patterns may have evolved between now and execution. Re-check the current Edit/Query screens for design language before designing this.
