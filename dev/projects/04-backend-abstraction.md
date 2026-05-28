# Project 04 — Backend abstraction (read path)  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 03.

## One-line goal

Introduce `LibraryBackend` ABC with `FeatherBackend` (wraps existing feather behaviour) and `SqliteBackend` (read path only). `Library` properties go through `backend.load_*()`. No mutation changes yet — mutations stay on the existing `save()` path.

## Decisions already locked

- `LibraryBackend` ABC in `src/archivum/backends/base.py`; impls in `feather.py` and `sqlite.py`.
- Backend selection via new `Configurator.backend: Literal["feather", "sqlite", "dual"]` field, default `feather`.
- Read-only abstraction in this project; mutations come in Project 05.
- `Library.{ref_df, doc_df, ref_doc_df, read_df}` properties call `backend.load_*()` then attach `.querex` at the Library layer (backend deals in plain DataFrames).
- Connection lifecycle: lesson from Project 01 informs default — probably per-Library-instance single connection.

## ABC surface for this project (read-only)

```python
class LibraryBackend(ABC):
    def load_refs(self) -> pd.DataFrame: ...
    def load_docs(self) -> pd.DataFrame: ...
    def load_ref_doc(self) -> pd.DataFrame: ...
    def load_read(self) -> pd.DataFrame: ...
    def data_version(self) -> int: ...      # for stale detection
    def close(self) -> None: ...
```

## Files to read first

- `src/archivum/library.py` lines ~311–393 — the four property loaders to wrap.
- `src/archivum/analytics/embeddings_store.py` (post-Project 01) — the SQLite patterns to mirror.
- `src/archivum/config.py` — where to add the `backend` field.
- `dev/projects/03-main-db-schema-migrator.md` once written for schema details.

## Open design questions (resolve when writing the brief)

- `FeatherBackend.data_version` — derive from file mtimes (current watchdog logic), or return a monotonic counter that bumps on every save? File mtimes preserve the existing semantics.
- Where does `FeatherBackend` get its querexfuzz config? Keep at Library layer (cleanest) — backend just returns plain frames.
- Should `Library.close()` exist now (to close the backend connection)? Currently there's no such method.

## Risks / gotchas

- `Library.reset()` currently nulls cached frames. With a backend, reset clears caches but the backend connection stays. Be careful about test fixtures that recreate libraries.
- The `.querex` accessor attachment must happen AFTER backend load, inside the property — backend returns plain DataFrames.

## Likely drift sources (revisit this stub after)

- **After Project 01:** connection lifecycle decision carries over.
- **Within the project:** the ABC may need to grow when Project 05 adds mutation methods. Design Project 04's surface minimally so we don't paint into a corner.
