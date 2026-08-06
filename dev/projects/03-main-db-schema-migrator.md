# Project 03 — Main DB schema + migrator  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 00, Project 02.

## One-line goal

Define the `archivum.db` schema (the SQL sketched in `dev/projects/README.md`) and write a one-shot `migrate_feathers_to_sqlite(lib)` with round-trip equality tests on a clone of uber-library-dev.

## Decisions already locked

- Schema as sketched in `dev/projects/README.md` — tables: `schema_meta`, `ref`, `doc`, `ref_doc`, `read`, `shelf`, `shelf_member`, `citation`, `change_log`.
- Hybrid ref schema: typed columns for every field in `config.ref_columns`, JSON `extras TEXT` for everything else.
- WAL mode, `synchronous=NORMAL`, foreign keys ON.
- Schema lives in `src/archivum/sql/archivum_v1.sql`, mirroring the Project 01 `embeddings_v1.sql` pattern.
- `Library.archivum_db_path` property — single source of truth for the path.
- Migrator idempotent: rerunning on a populated DB upserts or no-ops; never destroys data.
- Migrator backs up source feathers to `backups/migration-YYYYMMDD/` before any write.

## Files to read first

- `dev/projects/README.md` — full schema sketch.
- `src/archivum/library.py` lines ~323–393 — the four property loaders (column shapes, dtype expectations).
- `src/archivum/config.py` — `ref_columns` field.
- `src/archivum/configurations/querexfuzz-*-config.yaml` — what querexfuzz expects to find.
- `src/archivum/analytics/embeddings_store.py` (post-Project 01) — the SQLite patterns we landed on.

## Open design questions (resolve when writing the brief)

- Exact column list for `ref`: enumerate by introspecting a live uber-library DataFrame, not by trusting the static `ref_columns` list. Decide at execution.
- `doc.mod` / `create` / `access` storage format: ISO 8601 string with tz, or unix ns? ISO is human-readable; ns is lossless. Probably ISO + a tz suffix; verify nothing downstream needs ns precision.
- `doc.path` normalization: relative or absolute? Current feather code relativizes on save. Mirror that.
- `extras` JSON serialization: strict (`allow_nan=False`, NaN → null). `pd.NA` doesn't serialize cleanly; convert at the boundary.

## Risks / gotchas

- `pd.read_feather` returns object-dtype columns with mixed empty strings and NaN. Migrator must normalize NaN → NULL consistently.
- `version` int column on `doc` and `ref_doc` has its own allocation logic in `import_bibtex.assign_version`. Round-trip preserves the int; allocation is unchanged.
- `ref_doc` carries a **`priority`** int column (added 2026-08-06). 0 = the tag's primary document, 1+ = ordered alternates. It is NOT `version`: `version` selects which sharded copy of a content hash a row points at, `priority` orders the documents attached to one tag. Both are needed; the schema must carry both. The feather loader backfills a missing column via `utilities.assign_ref_doc_priority`; the migrator should persist whatever it finds and not re-derive. `(tag, hash, version)` remains the row identity, so `priority` is a plain column, not part of the key.
- `read.last_read` is `pd.Timestamp`; serialize as ISO with tz.
- The `querex` accessor column attached by querexfuzz is NOT data; never persist it. Verify the migrator strips it (current feather save does — mirror that).

## Likely drift sources (revisit this stub after)

- **After Project 01:** confirm the SQLite patterns from `EmbeddingStore` carry over (connection lifecycle, BLOB handling if needed, schema_meta convention).
- **Within the project:** the schema may need a tweak after we look at real data shapes. The README's sketch is a sketch.
