# Project 06 — Dual-write + backend-diff  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 05.

## One-line goal

Implement `backend: dual` mode (writes go to both feather and sqlite; reads come from sqlite) and a standalone `archivum backend-diff` CLI that round-trips both backends and reports any divergence. Run on T: dev rig until diffs are clean before going anywhere near prod.

## Decisions already locked

- Write order in dual mode: **feather first, then sqlite**, inside one Python-level tx (so we can roll back sqlite if it fails after a successful feather write).
- Reads in dual mode come from sqlite.
- `backend-diff` is a standalone CLI: round-trips all four tables, reports row-count + PK-set + per-row hash mismatches.
- "Clean enough to flip default" = several days of clean diffs on dev with real mixed use (reads, edits, an ingest). User pace is fast, so think days not weeks.

## ABC addition

```python
class DualBackend(LibraryBackend):
    def __init__(self, feather: FeatherBackend, sqlite: SqliteBackend): ...
    # Delegates reads to sqlite; writes to both, feather first.
```

## Files to read first

- `dev/projects/04-backend-abstraction.md` once written — the ABC contract.
- `dev/projects/05-mutation-rewrites.md` once written — the mutation method shapes.
- Output of running both backends side-by-side on dev (`backend-diff` first runs).

## Open design questions (resolve when writing the brief)

- What does `backend-diff` actually compare on `ref`? Row count + tag set + per-row hash of typed columns + extras JSON? Whatever's cheap to compute and catches drift.
- Should dual mode disable feather backups (the `backups/` rotation)? Yes — save the disk churn; we have two backends as our redundancy.
- Failure semantics when sqlite write fails after feather succeeded: log loudly + retry on next read? Roll back feather (hard)? Decide at execution.

## Risks / gotchas

- Dual-write doubles write latency. Acceptable for the transition window; not a forever thing.
- The diff tool will surface real bugs in the SQLite path — that's its job. Don't disable diffs to "make them pass."

## Likely drift sources (revisit this stub after)

- All of Project 05's outcomes; this project is the verification layer for them.
