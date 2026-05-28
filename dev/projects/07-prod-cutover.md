# Project 07 — Production cutover  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 06 done with clean diffs on dev.

## One-line goal

Migrate prod from feather → dual → sqlite. The only project that touches prod live. Run the migrator in-place on C: against prod's current feathers; flip to dual, run for a few days with daily `backend-diff`, then flip to sqlite. Re-ingest the accumulating bibtex from the user's migration-window workflow.

## Decisions already locked

- Migrator runs **in-place on C:**, against prod's current feathers. We do **NOT** copy dev's DB onto prod — it has diverged with days of test edits.
- Backup before migrate: snapshot all four feathers to `backups/pre-cutover-YYYYMMDD/` plus an additional copy into the Google-Drive-synced folder.
- Cutover sequence: pause prod → snapshot → migrate → dual mode → days of verification → sqlite mode.
- Operational workflow during the migration window: user keeps a single accumulating `pending-prod-ingest.bib` + a `pending-pdfs/` folder. At cutover, re-ingest the full file **in one shot** so tag allocator order matches dev.

## Cutover runbook outline (the meat of the brief)

1. Pause prod web/uber.
2. Snapshot prod feathers (twice — local backups + Google Drive).
3. Run migrator: `feather → archivum.db`. Verify row counts match feather counts.
4. Flip prod `config.yaml` to `backend: dual`. Restart web.
5. Run `backend-diff` immediately. Must be clean.
6. Re-ingest the accumulating bibtex: `archivum import-bibtex pending-prod-ingest.bib -p pending-pdfs -x`.
7. **Optional:** merge dev's `read` table rows into prod's `read` (UPSERT, sum counts, max last_read). Small script.
8. **Optional:** dump dev's `shelf` / `shelf_member` / `citation` and load into prod (if Projects 09/10 happened first).
9. Update user's tex editor to point back at prod `bibtex_file` (was repointed to dev during the migration window).
10. Several days of `backend: dual` with daily `backend-diff`.
11. Flip to `backend: sqlite`.

## Files to read first

- `dev/projects/05-mutation-rewrites.md` once written.
- `dev/projects/06-dual-write-diff.md` once written.
- Current prod `global-config.yaml` and library `config.yaml`.
- The accumulating `pending-prod-ingest.bib` from the user's workflow.

## Open design questions (resolve when writing the brief)

- Tag-allocator divergence: the accumulating-bibtex-in-one-shot recipe handles it iff the file's append order matches the dev ingest order. Verify in the brief; add a pre-cutover sanity check that lists tags dev would have assigned vs tags prod will assign.
- How many of the user's dev-side edits (`edit-tag`, `delete-tag`) need to be replayed in prod? Likely zero if user avoided edits in dev during the window. Pre-cutover checklist item.
- Should we test-rehearse the in-place migration on a fresh prod clone before doing it for real on actual prod? Yes — basically free insurance.

## Risks / gotchas

- **Highest-risk project in the plan.** Disciplined rollback rehearsal before D-day.
- The migrator is already proven on dev (Project 03), but the in-place run on C: needs its own test on a fresh prod clone before being run on actual prod.
- Rollback: flip `backend: feather` in `config.yaml`, restart. Prod is back to feather-mode on the snapshotted feathers. This must be tested before relying on it.

## Likely drift sources (revisit this stub after)

- Anything learned during the dual-write window in dev (Project 06).
- Operational changes in user's workflow during the migration window (more pending edits than expected, etc.).
