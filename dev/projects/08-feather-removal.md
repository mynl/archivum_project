# Project 08 — Feather removal + snapshot job  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 07 done; prod stable on `backend: sqlite` for several days.

## One-line goal

Delete `FeatherBackend`, all watchdog code, the `backups/` rotation, the `import-audit/` / `enhance-audit/` folder logic. Replace `Library.history()` with `change_log` reads. Add `archivum snapshot` command + Windows Task Scheduler entry for `VACUUM INTO` snapshots into the Google Drive folder.

## Decisions already locked

- Snapshot command: `VACUUM INTO <google-drive>/archivum-snapshots/<lib>-<YYYYMMDDTHHMMSS>.db`. Snapshots `archivum.db` and `embeddings.db` separately.
- Retention: 30 dailies + 12 monthlies (final cut TBD with user).
- Scheduling: Windows Task Scheduler entry; runbook in `dev/dev-environment.md`.
- `Library.history()` rewritten to read from `change_log` (`SELECT FROM change_log WHERE op='batch' AND source LIKE 'cli:import%' GROUP BY ...`).
- Never delete the pre-cutover snapshots (Project 07's `backups/pre-cutover-YYYYMMDD/`) — they're the "last known good before SQLite" artifact.

## What gets deleted

- `src/archivum/backends/feather.py` (entire file).
- `src/archivum/library.py`: `LibraryChangeHandler` class, `start_watcher`, `stop_watcher`, `_ignore_until`, `_last_mtimes`, `_cleanup_exports`, the per-save backup rotation in `save()`, the anti-wipe size checks (now obsolete with transactional writes).
- `src/archivum/library.py:history()` — rewritten, not deleted.
- All `pd.read_feather` / `pd.to_feather` calls outside of any migration scripts kept for reference.

## Files to read first

- `src/archivum/library.py` — full file, to identify everything to delete.
- `src/archivum/web/routes/shared.py:33-39` — watchdog hook (should already be gone after Project 05; verify).
- Whatever Project 06 produced in terms of dual-write code paths — also delete.
- Anything using `Library.history()` — grep before deleting.

## Open design questions (resolve when writing the brief)

- Retention numbers (30 + 12) — confirm with user.
- Should `archivum snapshot` also handle pruning per the retention policy, or is that a separate scheduled task? Probably one command does both.
- Move `import-audit/` / `enhance-audit/` data into `change_log` before deletion, or treat them as already-archived? Probably treat as archived — change_log is forward-only.

## Risks / gotchas

- `Library.history()` may have CLI callers we haven't audited. `rg "\.history\(\)"` before deleting.
- Don't delete the old `backups/` infrastructure until the new snapshot job has been running successfully for at least a week.

## Likely drift sources (revisit this stub after)

- Nothing significant — this is mechanical deletion + one new command.
