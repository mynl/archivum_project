    # dev/projects — SQLite migration project briefs

This directory holds **cold-start instruction docs** for the multi-session SQLite migration. Each numbered `NN-name.md` is self-contained: a fresh Claude (or human) session opens one file, has everything needed to do that chunk of work, and does not need to read sibling briefs.

`docs/` is reserved for Sphinx documentation. Do **not** put planning material there.

## Why this migration exists

The current `Library` backs onto three feather files (`ref.feather`, `doc.feather`, `ref-doc.feather`) plus `read.feather`. The save model is all-or-nothing: every mutation rewrites all four feathers, regenerates `bibtex.bib`, takes a backup, and forces a `reset()`. This means:

- Every PDF view triggers a full bibtex rewrite via `record_read → save → write_bibtex` (acknowledged hotspot).
- Cross-process changes need a watchdog with 100ms sync-tolerance logic to detect external edits.
- Adding features like shelves or per-row citation tracking is structurally awkward.
- Save-as-written is impossible — the user can never just "edit a tag and have it persist."

Target end-state: SQLite per library (`archivum.db`, WAL mode), embeddings in a separate `embeddings.db`, write-through mutations with a per-row `change_log` for rollback, watchdog deleted (replaced by `PRAGMA data_version` based stale-detection), new shelves/citations tables.

## Working agreement

The whole migration is being done on an **insulated T: drive dev rig** — separate git worktree on the `sqlite-migration` branch, copied library + doc store + full text, dedicated `.venv`, port 9125. Prod on C: keeps running untouched. See `00-dev-rig.md` for setup. Aviation-style QA: no migration code touches prod until Project 7's cutover, and that cutover happens against prod's *current* feathers (not the T: dev DB, which will have diverged from days of test edits).

**Expected pace: days, not weeks.** Working with Claude the whole arc is expected to fit in a small number of focused sessions. The dual-write verification window in dev (Project 06) and in prod (Project 07) is "days of clean diffs," not weeks.

## Project file convention: stubs now, full briefs at execution time

Projects 00 and 01 have **full briefs** because they're imminent. Projects 02–11 currently exist as **stubs** — each captures what we've already decided + files to read first + open design questions + drift sources, but stops short of the full execution-ready brief. The stub is intentionally minimal: writing full briefs weeks ahead of execution wastes effort because earlier projects' outcomes invalidate later plans (e.g. Project 04's backend ABC is shaped by what we learn from Project 01's `EmbeddingStore`).

**Rule:** convert the next project's stub to a full brief in the session that executes it, drawing on what was actually learned in the projects before. The stub's "Likely drift sources" section names which earlier-project outcomes should specifically be re-checked at conversion time.

## Decisions already locked (do not relitigate without cause)

- **Single-machine, single-user.** Web ↔ uber shell auto-sync. Jupyter does not auto-sync (caller calls `lib.reset()`).
- **DB at a real local path, NOT in a cloud-synced directory.** Backups via `VACUUM INTO` snapshots into a Google-Drive-synced folder.
- **Ref schema: hybrid.** Typed columns for everything in `config.ref_columns`; JSON `extras` column for the long tail of sparse BibTeX fields.
- **Shelves: tag-keyed.** `shelf(id, name, ...) + shelf_member(shelf_id, tag, position, added_at)`.
- **Transition: dual-write window** (days, not weeks — fast-pace working) then drop feather.
- **Citations: separate table** `citation(tag PK, count, source, fetched_at)`, lazily populated from OpenAlex on search-result interaction; "high-quality" filter TBD.
- **Logging: per-row `change_log` table** in the DB. The current paranoid file-based audit (`import-audit/`, `enhance-audit/`) gets retired post-migration; `Library.history()` reads from `change_log` instead.
- **Bibtex regen stays tight** for ref-mutating ops (the @new-ref immediate-citeable behaviour is a hard differentiator vs Mendeley/Zotero). Skipped for `record_read`, `link_document`, `validate`, `update_hashes`.

## The 12 projects

Dependency chain runs straight down. Projects 0–6 happen entirely on the T: rig. Project 7 is the only one that touches prod live. 9, 10, 11 are independent of each other.

| # | Brief | Goal | Deps | Status |
|---|---|---|---|---|
| 00 | `00-dev-rig.md` | T: worktree + 4 safety-belt code edits + library/doc-store/text copy + isolation smoke tests | — | **brief ready** |
| 01 | `01-embeddings-sqlite.md` | Migrate `semantic-embeddings.feather` to per-library `embeddings.db`. Smallest target; validates SQLite patterns. | 00 | **brief ready** |
| 02 | `02-library-clone.md` | CLI `archivum library-clone <src> <dest>` formalizing the manual setup from Project 0. | 00 | stub |
| 03 | `03-main-db-schema-migrator.md` | Define `archivum.db` schema + one-shot `feather → sqlite` migrator. Round-trip tested on uber-library clone. | 00, 02 | stub |
| 04 | `04-backend-abstraction.md` | `LibraryBackend` ABC + `FeatherBackend` + `SqliteBackend` (read path only). `Library` properties go through the backend. | 03 | stub |
| 05 | `05-mutation-rewrites.md` | Rewrite the 10 `save()`-sites as backend transactions with `change_log`. Delete watchdog. `PRAGMA data_version` for cross-process refresh. Drop bibtex regen from `record_read`. | 04 | stub |
| 06 | `06-dual-write-diff.md` | `backend: dual` mode (write both feather + sqlite, read sqlite) + `archivum backend-diff` integrity tool. Run on T: until clean. | 05 | stub |
| 07 | `07-prod-cutover.md` | Prod runbook: pause, snapshot feathers, run migrator in-place on C:, flip to dual, run for several days, flip to sqlite. | 06 | stub |
| 08 | `08-feather-removal.md` | Delete `FeatherBackend`, watchdog code, `backups/`, `import-audit/`, `enhance-audit/`. New `archivum snapshot` command + Task Scheduler entry. | 07 | stub |
| 09 | `09-shelves.md` | `shelf` + `shelf_member` tables + web UI + CLI. | 07 | stub |
| 10 | `10-citations-openalex.md` | `citation` table + OpenAlex client + lazy backfill from search results. | 07 | stub |
| 11 | `11-mendeley-onramp.md` | `archivum init-from-mendeley` zero-config command + refactor `definitive_loader.py` patterns for generic use. | 07 | stub |

```
00 ──┬── 01
     └── 02 ── 03 ── 04 ── 05 ── 06 ── 07 ── 08
                                          ├── 09
                                          ├── 10
                                          └── 11
```

## Brief structure convention

Every `NN-name.md` follows this shape so a cold session knows where to look:

1. **Goal** — one paragraph.
2. **Prereqs** — which projects must be done; what state the rig must be in.
3. **Files to read first** — the 5–10 source files a cold session needs in context before touching anything.
4. **Plan of attack** — ordered steps. Code sketches inline where useful.
5. **New code surface** — schemas, class skeletons, new CLI commands.
6. **Acceptance tests (DoD checklist)** — concrete, tickable.
7. **Rollback** — how to undo before the work merges to master.
8. **PR description draft** — for when the work is done.

## Related project context

- `CLAUDE.md` (repo root) — conventions, especially PowerShell-only + `rg` rules.
- `human-notes.md` — user's running roadmap, including the May-13 improvement notes that motivate several of the migration's secondary goals (e.g. `record_read` write amplification).
- Stored memory (in Claude's session memory): `project-sqlite-migration`, `project-doc-layout`, `feedback-shell-powershell-rg`.
