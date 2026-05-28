# Project 01 — Embeddings → SQLite

**Status:** queued
**Branch:** `sqlite-migration`
**Worktree:** `T:\archivum-dev\src`
**Scope:** semantic embeddings only. Main DB is Project 03.

## Goal

Migrate the per-library `semantic-embeddings.feather` file to a per-library SQLite database `embeddings.db`. This is the smallest possible target for first real SQLite work: one consumer (`analytics/semantic.py`), one schema, one migrator, no cross-cutting changes. The objective is to **validate the SQLite patterns we'll use everywhere else** — WAL mode, BLOB storage, `model_version` tracking, idempotent migration, round-trip tests — on a piece of the system where the worst case is "re-encode some papers" rather than "lose metadata."

Deliberately **no backend abstraction**. One reader, one writer, direct `sqlite3` calls in a small helper. Project 04 introduces the `LibraryBackend` ABC for the main DB; for embeddings, one consumer doesn't justify the indirection.

## Prereqs

- **Project 00 done.** T: rig running, isolation smoke tests all green. Dev library `uber-library-dev` opens cleanly.
- `semantic-embeddings.feather` exists in the dev library (`T:\archivum-dev\AppData\Local\archivum\libraries\uber-library-dev\`). If not — because no semantic query has been run there yet — kick one off first so we have a real feather to migrate against. Quickest path: open the dev web (port 9125), Network page, run `q top 20 title ~ /risk measure/` in semantic mode. The feather will appear.
- Verify the dev feather size matches prod's — confirms the rclone in Project 00 preserved it. `Get-Item ...\semantic-embeddings.feather | Select-Object Length, LastWriteTime` in both libraries.

## Files to read first

- `src/archivum/analytics/semantic.py` — the **only** consumer. Sites that touch the feather:
  - Lines 192–198: `_source_embedding_index()` helper.
  - Lines 385–390: load (`pd.read_feather`).
  - Lines 442–445: write (`pd.concat ... drop_duplicates ... to_feather`).
  - Surrounding context (lines 354–556): the `analyze_semantic()` function that orchestrates universe resolution → embedding lookup → encode missing → UMAP → HDBSCAN.
- `src/archivum/library.py` — `Library.__init__` for where to add `embeddings_db_path` property.
- `src/archivum/analytics/semantic.py` line 66: `SEMANTIC_MODEL_NAME = "all-MiniLM-L6-v2"` — the model identifier we'll store in `model_version`.
- Downstream consumers of `SemanticResult.relevant_idx` (must keep returning a DataFrame with `hash`, `source`, `embedding` columns — do **not** change this contract):
  - `src/archivum/quarto.py` lines 372, 609, 684 (report rendering).
  - `src/archivum/web/services/network.py` line 41.
  - `src/archivum/web/routes/network.py` lines 68, 91.
  - `src/archivum/web/routes/reports.py` line 165.
- `tests/test_semantic_model_cache.py` for the test harness pattern.
- `dev/projects/README.md` — locked decisions, esp. the schema sketch.

## Plan of attack

1. **Schema sketch** in a `schema.sql` resource file under `src/archivum/analytics/sql/embeddings_v1.sql`. One source of truth, loaded by both the store and the migrator.
2. **Write the `EmbeddingStore` helper** (`src/archivum/analytics/embeddings_store.py`) — a small class wrapping a single `sqlite3.Connection`, with the methods `semantic.py` actually needs (see "New code surface" below).
3. **Add `Library.embeddings_db_path` property** so paths are computed in one place.
4. **Write the one-shot migrator** (`src/archivum/migrations/embeddings_v1.py`) + CLI entry point `archivum migrate-embeddings`. Idempotent. Backs up the source feather to `.bak` before doing anything.
5. **Rewrite the two feather sites in `semantic.py`** to use `EmbeddingStore`. Keep `_source_embedding_index()` deleted or repurposed — most of its logic moves into the store.
6. **Tests** — round-trip, idempotency, BLOB encoding correctness, `model_version` populated, query-time DataFrame shape preserved.
7. **Run the migrator on the dev library**, then run the full web semantic flow on T: and watch the verbose log messages.

## New code surface

### `src/archivum/analytics/sql/embeddings_v1.sql`

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS embeddings (
  hash          TEXT    NOT NULL,
  source        TEXT    NOT NULL,        -- 'title' | 'text' | 'text-4k'
  embedding     BLOB    NOT NULL,        -- np.float32 array via .tobytes()
  dim           INTEGER NOT NULL,        -- 384 for all-MiniLM-L6-v2
  model_version TEXT    NOT NULL,        -- e.g. 'all-MiniLM-L6-v2/v1'
  encoded_at    TEXT    NOT NULL,        -- ISO 8601, e.g. '2026-05-24T14:32:11+00:00'
  PRIMARY KEY (hash, source)
);

CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- Rows we expect at v1: ('schema_version', '1'), ('created_at', <ISO>), ('model_name', 'all-MiniLM-L6-v2')
```

### `src/archivum/analytics/embeddings_store.py`

Sketch — the surface that `semantic.py` actually needs. Keep it small; resist adding methods speculatively.

```python
import sqlite3
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd


SCHEMA_VERSION = 1
SCHEMA_RESOURCE = "archivum.analytics.sql.embeddings_v1.sql"


class EmbeddingStore:
    """Per-library SQLite store for sentence-transformer embeddings.

    Schema is hash-source keyed: (hash, source) is the primary key.
    Embeddings stored as float32 BLOBs; dim recorded explicitly so we can
    np.frombuffer + reshape on read without trusting the model.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def _ensure_schema(self):
        sql = (files("archivum.analytics") / "sql" / "embeddings_v1.sql").read_text(encoding="utf-8")
        with self._conn:
            self._conn.executescript(sql)
            cur = self._conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )

    def fetch_for_source(self, source: str) -> pd.DataFrame:
        """Return DataFrame with columns [hash, source, embedding] for this source.

        embedding is a Python list[float] to match the existing feather shape
        consumed by analyze_semantic / UMAP.
        """
        cur = self._conn.execute(
            "SELECT hash, source, embedding, dim FROM embeddings WHERE source = ?",
            (source,),
        )
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=["hash", "source", "embedding"])
        records = [
            {
                "hash": h,
                "source": s,
                "embedding": np.frombuffer(blob, dtype=np.float32).reshape(dim).tolist(),
            }
            for (h, s, blob, dim) in rows
        ]
        return pd.DataFrame(records)

    def cached_hashes(self, source: str) -> set[str]:
        cur = self._conn.execute("SELECT hash FROM embeddings WHERE source = ?", (source,))
        return {row[0] for row in cur.fetchall()}

    def upsert_many(
        self,
        items: list[tuple[str, str, np.ndarray]],
        *,
        model_version: str,
    ) -> int:
        """UPSERT a batch of (hash, source, embedding_ndarray) rows. Returns row count."""
        if not items:
            return 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = []
        for h, source, emb in items:
            arr = np.asarray(emb, dtype=np.float32)
            payload.append((h, source, arr.tobytes(), int(arr.size), model_version, now))
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO embeddings(hash, source, embedding, dim, model_version, encoded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash, source) DO UPDATE SET
                  embedding = excluded.embedding,
                  dim = excluded.dim,
                  model_version = excluded.model_version,
                  encoded_at = excluded.encoded_at
                """,
                payload,
            )
        return len(payload)

    def row_count(self, source: str | None = None) -> int:
        if source:
            cur = self._conn.execute("SELECT COUNT(*) FROM embeddings WHERE source = ?", (source,))
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM embeddings")
        return int(cur.fetchone()[0])

    def close(self):
        self._conn.close()
```

Open question for execution: keep one shared connection per process (above) or open per-call. SQLite WAL is fine with either; one-per-process is faster and what the web app will want. Document this in the docstring.

### `Library.embeddings_db_path`

Add to `src/archivum/library.py`:

```python
@property
def embeddings_db_path(self) -> Path:
    return self.config_path / "embeddings.db"
```

That's it. Library does **not** own an `EmbeddingStore` instance — `analytics/semantic.py` opens its own. The Library is just the path authority.

### `src/archivum/migrations/embeddings_v1.py`

```python
"""One-shot migrator: semantic-embeddings.feather -> embeddings.db (schema v1).

Idempotent. Safe to rerun. Backs up the source feather before doing anything.
"""
import logging
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from ..analytics.embeddings_store import EmbeddingStore, SCHEMA_VERSION
from ..analytics.semantic import SEMANTIC_MODEL_NAME

logger = logging.getLogger(__name__)


def migrate_embeddings(lib, *, dry_run: bool = False) -> dict:
    feather_path = lib.config_path / "semantic-embeddings.feather"
    db_path = lib.embeddings_db_path
    backup_path = feather_path.with_suffix(".feather.bak")

    if not feather_path.exists():
        logger.info("No source feather at %s; nothing to migrate.", feather_path)
        return {"migrated": 0, "skipped": 0, "feather_rows": 0}

    df = pd.read_feather(feather_path)
    feather_rows = len(df)
    logger.info("Source feather has %d rows.", feather_rows)

    if dry_run:
        return {"migrated": 0, "skipped": 0, "feather_rows": feather_rows, "dry_run": True}

    if not backup_path.exists():
        shutil.copy2(feather_path, backup_path)
        logger.info("Backed up source feather to %s.", backup_path)

    store = EmbeddingStore(db_path)
    existing = {
        (row[0], row[1])
        for row in store._conn.execute("SELECT hash, source FROM embeddings").fetchall()
    }

    items = []
    skipped = 0
    for _, row in df.iterrows():
        key = (str(row.hash), str(row.source))
        if key in existing:
            skipped += 1
            continue
        emb = np.asarray(row.embedding, dtype=np.float32)
        items.append((key[0], key[1], emb))

    migrated = store.upsert_many(items, model_version=f"{SEMANTIC_MODEL_NAME}/v1")
    store.close()

    logger.info("Migrated %d rows; skipped %d already present.", migrated, skipped)
    return {
        "migrated": migrated,
        "skipped": skipped,
        "feather_rows": feather_rows,
        "schema_version": SCHEMA_VERSION,
    }
```

CLI entry point in `src/archivum/cli.py`:

```python
@entry.command(name="migrate-embeddings")
@click.option("--dry-run", is_flag=True, help="Report what would migrate, don't write.")
def migrate_embeddings_cmd(dry_run):
    """Migrate semantic-embeddings.feather to embeddings.db (idempotent)."""
    from .migrations.embeddings_v1 import migrate_embeddings
    lib = LibraryContext.get()
    if lib.is_empty:
        click.echo("No library open.")
        return
    result = migrate_embeddings(lib, dry_run=dry_run)
    click.echo(json.dumps(result, indent=2))
```

### Rewrites in `src/archivum/analytics/semantic.py`

Replace the two feather sites with `EmbeddingStore` calls. `_source_embedding_index()` becomes redundant — its work (filter by source, get cached hashes) is now two store methods. The shape of `relevant_idx` returned to downstream consumers (`hash`, `source`, `embedding` cols, `iterrows()` semantics) **must not change** — verify by re-running quarto report generation and network page semantic mode after the rewrite.

The new flow inside `analyze_semantic`:

```python
# old:
#   idx_path = lib.config_path / "semantic-embeddings.feather"
#   idx_df = pd.read_feather(idx_path) if idx_path.exists() else ...
#   source_idx, cached_hashes = _source_embedding_index(idx_df, source_type)

# new:
store = EmbeddingStore(lib.embeddings_db_path)
source_idx = store.fetch_for_source(source_type)
cached_hashes = set(source_idx["hash"].astype(str)) if not source_idx.empty else set()
source_idx_count = len(source_idx)
```

…and where the feather is rewritten:

```python
# old:
#   idx_df = pd.concat([idx_df, pd.DataFrame(new_rows)]).drop_duplicates(["hash","source"], keep="last")
#   idx_df.reset_index(drop=True).to_feather(idx_path)
#   source_idx, cached_hashes = _source_embedding_index(idx_df, source_type)

# new:
items = [
    (h, source_type, np.asarray(emb, dtype=np.float32))
    for h, emb in zip(embed_hashes, embedding_values)
]
store.upsert_many(items, model_version=f"{SEMANTIC_MODEL_NAME}/v1")
# Refresh the source_idx for the now-cached rows:
source_idx = store.fetch_for_source(source_type)
cached_hashes = set(source_idx["hash"].astype(str))
source_idx_count = len(source_idx)
```

Close the store at the end of `analyze_semantic` (or hold one connection per library on a module-level dict; decide during execution).

## Acceptance tests — DoD checklist

### Unit tests (`tests/test_embeddings_store.py`)

- [ ] `EmbeddingStore` creates the DB file with WAL mode on first instantiation.
- [ ] `schema_meta` has `schema_version = '1'` after init.
- [ ] `upsert_many` followed by `fetch_for_source` round-trips: same `(hash, source)` keys; embedding floats equal to within `np.allclose(atol=0, rtol=0)` (we're storing then reading the same bytes — should be exact).
- [ ] `upsert_many` is idempotent: calling twice with the same items produces the same row count.
- [ ] `upsert_many` with conflicting `(hash, source)` updates the existing row (new `encoded_at`, new bytes).
- [ ] `dim` column populated correctly (384 for `all-MiniLM-L6-v2`).
- [ ] `model_version` round-trips.
- [ ] `cached_hashes(source)` returns only the requested source's hashes.

### Migrator tests (`tests/test_migrate_embeddings.py`)

- [ ] Empty source feather → `migrated=0, skipped=0, feather_rows=0`.
- [ ] Full migrate: every `(hash, source)` from the feather appears in the DB exactly once. Counts match.
- [ ] Embedding values byte-identical between feather (list of float) and DB (float32 BLOB → np.frombuffer → tolist). Within `np.allclose(atol=1e-6)` accounting for the float64→float32 narrowing if the feather happens to store float64. (Sanity-check actual dtype in the existing feather first.)
- [ ] Idempotent: rerun on the same feather → `migrated=0, skipped=N`.
- [ ] Source feather backed up to `.bak` on first run; second run does not overwrite the `.bak`.
- [ ] `--dry-run` writes nothing.

### Integration on the T: dev rig

- [ ] `uv run archivum migrate-embeddings` on `uber-library-dev` reports nonzero `migrated` count matching feather rows.
- [ ] `T:\...\uber-library-dev\embeddings.db` exists; `Get-Item` shows non-trivial size.
- [ ] `T:\...\uber-library-dev\semantic-embeddings.feather.bak` exists.
- [ ] Dev web (port 9125), Network page, run `q top 20 title ~ /risk measure/` in semantic mode. Verbose log shows `Embeddings used: N (M cached, K new)` consistent with DB content. Page renders galaxy.
- [ ] Same query again — second run should hit fully cached (`K new = 0`), runs faster.
- [ ] Switch to text-4k source, run again — should encode new embeddings, write to DB, render galaxy. Re-check: `SELECT source, COUNT(*) FROM embeddings GROUP BY source;` shows both `title` and `text-4k` rows.
- [ ] Quarto semantic report generation on dev (`Reports` page) renders SVGs that include cluster hulls — confirms `relevant_idx` DataFrame shape preserved.
- [ ] Compare elapsed time on a warm semantic query vs the old feather backend (verbose log timings). Should be **not worse** than feather mode. If it's measurably worse, profile before merging.
- [ ] After all that: `Test-ArchivumWeb.ps1 -Mode Slow` passes on dev with `--run-slow-web` (semantic tests included).
- [ ] **Prod untouched:** `C:\...\uber-library\semantic-embeddings.feather` mtime unchanged; no `embeddings.db` exists in prod library dir.

### Code hygiene

- [ ] `pd.read_feather` and `pd.to_feather` for embeddings no longer appear in `semantic.py` (`rg "semantic-embeddings" src/`).
- [ ] `_source_embedding_index` either deleted or has all callers removed.
- [ ] `lib.embeddings_db_path` is the only place the `embeddings.db` path is constructed (`rg "embeddings.db" src/`).

## Rollback

If something goes wrong before merging:

1. `git checkout sqlite-migration` and `git revert <commit-sha>` for the embeddings commit(s). Revert is preferred over reset on a shared branch.
2. On the dev library, `Remove-Item T:\...\uber-library-dev\embeddings.db`.
3. `Move-Item T:\...\uber-library-dev\semantic-embeddings.feather.bak T:\...\uber-library-dev\semantic-embeddings.feather` to restore.
4. Restart dev web; semantic mode should work as before.

If something goes wrong **after** merging but before Project 03 is started: same rollback applies — embeddings code is isolated to `analytics/semantic.py`, the new helper module, the migrator, and `lib.embeddings_db_path`. Easy to revert in one PR.

## PR description draft

```
Project 01: Embeddings → SQLite

Migrates semantic embeddings from per-library semantic-embeddings.feather to
per-library embeddings.db (SQLite, WAL mode).

- New EmbeddingStore (analytics/embeddings_store.py): thin wrapper over sqlite3,
  hash-source keyed, BLOB-encoded float32 embeddings, dim + model_version recorded.
- New one-shot migrator (migrations/embeddings_v1.py + archivum migrate-embeddings):
  idempotent, backs up the source feather to .bak before writing.
- Library.embeddings_db_path property — single point of truth for the path.
- analytics/semantic.py: the two feather sites (read + write) replaced with
  EmbeddingStore calls. _source_embedding_index helper retired.
- Downstream contract preserved: SemanticResult.relevant_idx is still a DataFrame
  with hash/source/embedding columns, so quarto report rendering and the Network
  page consume it unchanged.

No backend abstraction yet — one consumer doesn't justify it. The main-DB
abstraction comes in Project 04.

Tested on T: dev rig: round-trip migrator equality, idempotency, semantic web
query end-to-end (cold then warm), Quarto report rendering, Network page semantic
mode.

Prod untouched (T: rig is fully isolated per Project 00).
```

## Notes for the executing session

- The user explicitly called this "very discrete and separate and easy" and wanted it ASAP. Match that — resist the urge to add features like vector indexing, multi-model support, or a CLI for inspecting embeddings. Those can come later if needed.
- Confirm the existing feather's embedding dtype before assuming float32. If it's float64 in the feather (likely), the migrator narrows to float32 — flag this explicitly in the PR description as an intentional precision change (saves ~50% disk, and we re-encode if it ever matters).
- The `EmbeddingStore` connection lifecycle is the one thing worth thinking about. Three options: per-call (simple, slow), one-per-process module-level dict keyed by library path (fast, leaks if libraries change), per-Library-instance (cleanest, but `Library` doesn't currently own a store). I'd lean per-call for the first version — semantic queries aren't that frequent — and revisit only if profiling shows it as a bottleneck.
- The `model_version` string `"all-MiniLM-L6-v2/v1"` is a placeholder convention. If we change models later, bump the `/vN`. Not worth a registry now; one entry.
- After the work merges to `sqlite-migration` branch, **do not** propagate the migration to prod. Prod migration happens in Project 07 (cutover). The dev DB is the source of truth on T: only.
- The "performance not worse than feather" check is loose — feather loads the whole file every semantic call, SQLite loads only the source we asked about, so for a single-source query SQLite should be the same or better. If it's worse, something's wrong (likely connection setup overhead) — investigate, don't ship.
