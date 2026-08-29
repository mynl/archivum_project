# Plan 2.5.0 — move the app home to `~/.archivum`, split settings and data onto `D:`

Status: reviewed, approved in design, **not started**. Target version **2.5.0** (behavior change
→ minor bump).

---

## 1. Goal

Archivum currently keeps its app home in the hidden Windows location
`%LOCALAPPDATA%\archivum`. Every other project in `V:\dev` now uses `~/.<appname>` with a
`<APP>_HOME` environment override. Archivum is the last holdout.

At the same time the machine has moved (KOLMOGOROV → DOOB) and the library data has not been
brought across yet. The user has staged it on `D:` in two places, and wants the app home to
reach it by symlink so that *where the bytes physically live is a backup decision, not an
application decision*.

Four things happen together, in this order:

1. **[home]** `BASE_DIR` moves from `%LOCALAPPDATA%\archivum` to `~/.archivum`, overridable
   with `$ARCHIVUM_HOME`. The home is no longer auto-populated: a missing global config or an
   empty, unlinked store is an error, not a first run.
2. **[copy]** Every `hardlink_to` in the package becomes a copy. On KOLMOGOROV everything sat
   on `C:` and hardlinks were free; on DOOB the store is on `D:` and sources (Downloads, web
   staging on `V:`) are not, and hardlinks cannot cross volumes. The `.bak` files, which were
   hardlinks and therefore never backups (§4.1), are fixed by the same change.
3. **[wiring]** `~/.archivum` is populated with directory and file symlinks pointing at
   `D:\archivum` (data) and `D:\Settings\archivum` (settings), so archivum runs on DOOB for the
   first time.
4. **[service]** The WinSW service gets `ARCHIVUM_HOME` pinned so it does not depend on the
   service account's profile.

Steps 1 and 2 are code. Step 3 is a one-time operator procedure, scripted. Step 4 is a settings
edit outside the repo.

---

## 2. Current behavior

### 2.1 How the app home is resolved

`src/archivum/__init__.py:23-41` — this is the **only** place in the codebase that names
`LOCALAPPDATA`:

```python
def _get_local_folder() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    app_data = base / __appname__
    if not app_data.exists():
        app_data.mkdir(parents=True, exist_ok=True)
    return app_data

BASE_DIR = _get_local_folder()
LIBRARIES_DIR = BASE_DIR / "libraries"
GLOBAL_CONFIG_PATH = BASE_DIR / "global-config.yaml"
LIBRARIES_DIR.mkdir(exist_ok=True)
```

Directly below, `_load_global_config()` **writes a default `global-config.yaml`** if none exists.
That matters for the wiring order (§5.3): merely importing archivum drops a real file at what is
meant to be a symlink site.

Everything else derives from `BASE_DIR` and needs no change:

| consumer | what it derives |
|---|---|
| `__init__.py:85 resolve_path()` | resolves any non-absolute config path against `BASE_DIR` |
| `analytics/semantic.py:71` | `BASE_DIR / "models" / "sentence-transformers"` |
| `cli.py:1345` | `BASE_DIR / "query_history.txt"` |
| `library.py:70` | `self.config_path = LIBRARIES_DIR / <library name>` |
| `library.py:78,81,87` | `doc_store_path`, `text_dir_path`, `debug_dir_path` via `resolve_path` |
| `library.py:89` | `exports_dir_path = self.config_path / "exports"` |

### 2.2 What the app home contains, and what that means for backup

| what | path under the home | actual size here | disposition |
|---|---|---|---|
| global config | `global-config.yaml` | tiny | **settings → `D:\Settings\archivum`** |
| library config | `libraries/uber-library/config.yaml` | tiny | **settings → `D:\Settings\archivum`** |
| library data | `libraries/uber-library/{ref,doc,ref-doc,read}.feather` | 3.5 MB | **data → `D:\archivum`**, precious |
| BibTeX projection | `libraries/uber-library/bibtex.bib` | small | data, regenerable from `ref.feather` |
| document store | `docs/` (`doc_store_lib`) | **28.9 GB, 7,648 files** | **data → `D:\archivum\ShardedDocLibrary`** |
| extracted text | `full-text/` (`full_text_lib`) | 1.55 GB, 7,504 files | data, derived, not backed up |
| report sources | `libraries/uber-library/exports/` | small | reproducible, not backed up |
| embeddings cache | `libraries/uber-library/semantic-embeddings.feather` | large | derived, not backed up |
| import audit | `libraries/uber-library/import-audit/` | small | derived, not backed up |
| model cache | `models/sentence-transformers/` | ~90 MB | derived, not backed up |
| query history | `query_history.txt` | tiny | local, not backed up |
| debug output | `C:/tmp/archivum_debug` (absolute in config) | grows | already outside the home |

**Note the trap:** `doc_store_lib` and `full_text_lib` are the *relative* strings `docs` and
`full-text` in the staged `global-config.yaml`. `resolve_path()` resolves those against
`BASE_DIR`, so on a naive first run archivum would create the document store *inside*
`~/.archivum`. The symlinks in §5.3 are what prevent that, and they must exist **before** the
first run. §5.1 turns that from a silent mistake into a hard error.

### 2.3 What is staged on `D:` right now

```
D:\Settings\archivum\          global-config.yaml, config.yaml,
                               ref.feather, doc.feather, ref-doc.feather, read.feather
D:\archivum\
  ShardedDocLibrary\           7,648 files, 28.9 GB
  ShardedFullText\             7,504 files, 1.55 GB
  Biblio\                      4 .csl files
```

The four feather files are currently in the **Settings** folder. They are data, not settings:
this plan moves them to `D:\archivum\libraries\uber-library\`. `D:\Settings\archivum` keeps
only the two YAML files.

`bibtex.bib` is not staged. `write_bibtex` regenerates it from `ref.feather` on first save; no
action needed.

### 2.4 The staged settings

`D:\Settings\archivum\config.yaml` (library `uber-library`) has already been repointed by the
user from the KOLMOGOROV path `\S\Telos\biblio\uber-library.bib` to:

```yaml
bibtex_file: D:\Projects\Biblio\uber-library.bib
debug_dir: C:\tmp\archivum_debug                   # duplicates the global value; harmless
```

`D:\Projects\Biblio\` exists and holds four `.csl` files; `uber-library.bib` is not there yet.

Staged `global-config.yaml` also carries `full_text: true`, which is not in
`DEFAULT_GLOBAL_CONFIG`. `load_configuration` will print a legacy-field warning. Harmless;
noted so it is not mistaken for a symptom.

Housekeeping, not this plan's business: `D:\archivum\Biblio\` and `D:\Projects\Biblio\` hold
the same four `.csl` files. The writing tree is the natural keeper; delete the other copy.

---

## 3. Design decision: directory symlinks, not per-file symlinks

The house precedent (`~/.great-servers/servers.yaml` → `D:\Settings\great-servers\servers.yaml`)
is a **per-file** symlink inside a real directory. **That approach must not be used for the
library directory here**, because it silently breaks auto-reload.

`Library.start_watcher` (`library.py:139-146`) schedules a watchdog observer on `config_path`
with `recursive=False`, and the handler reacts to writes to `ref.feather`, `doc.feather`, and
`ref-doc.feather`. That is what lets the web app notice edits made from the CLI, via the
`needs_reload` flag and the before-request hook.

Measured on this machine (`dev/` scratch test, watchdog as installed):

| arrangement | watcher fires? |
|---|---|
| watch the real `D:` directory directly | yes (baseline) |
| **`~/.archivum/libraries/uber-library` is a directory symlink → `D:`** | **yes** |
| real `C:` directory, `ref.feather` is a file symlink → `D:` | **NO** |

Reason: a directory symlink opened without `FILE_FLAG_OPEN_REPARSE_POINT` resolves to the target
directory, so `ReadDirectoryChangesW` watches the real directory on `D:` and sees the writes. A
file symlink inside a watched directory does not — the write lands on the target on `D:` and
the `C:` directory entry never changes, so no event is emitted.

**Therefore:**

- Anything whose *directory* is watched or scanned → **directory symlink**.
- Individual settings files that nothing watches → file symlink is fine.
  `global-config.yaml` is read once at import. `config.yaml` is not in the watcher's
  `core_files` set. Both are safe as file symlinks.

Consequence worth stating: because `libraries\uber-library` is itself a directory link, the
`config.yaml` file symlink *inside* it physically lives at
`D:\archivum\libraries\uber-library\config.yaml`, and `Configurator.save()` writes `config.bak`
beside it on `D:\archivum`, not in `D:\Settings`. Writes through the file link land in
`D:\Settings\archivum\config.yaml` as intended.

---

## 4. Hardlinks: why every one of them goes

### 4.1 The `.bak` files are not backups

Two places do the same thing:

- `src/archivum/config.py:113-117` — `Configurator.save()`
- `src/archivum/library.py:1189-1193` — `Library.write_bibtex()`

```python
bak_path.hardlink_to(file_path)          # second name for the SAME inode
...
with file_path.open("w") as f: ...       # truncates that inode in place
```

A hardlink is another name for one inode; `open(..., "w")` truncates in place rather than
replacing. So the "backup" is truncated and rewritten along with the original. Verified:

```
config.yaml -> 'rewritten: yes\n'
config.bak  -> 'rewritten: yes\n'
backup preserved original? False
```

There is presently **no config or BibTeX backup at all**, on any machine, and there never has
been.

### 4.2 Sharding and ingest cannot cross volumes

Every place a document or audit file enters the library does so by `hardlink_to`:

| site | what it links |
|---|---|
| `enhancements.py:863, 1055` | shard a document into the store |
| `utilities.py:483` | re-shard on rename / reorganize |
| `web/services/ingest_batch.py:144` | stage an upload (already falls back to copy) |
| `import_bibtex.py:1426` | copy audit files into `config_path/import-audit/` |
| `import_bibtex.py:1524` | audit copy of the input `.bib` |

Hardlinks cannot cross volumes. With the store on `D:` and sources on `C:` (Downloads) or `V:`
(web staging is `temp/staging` under the checkout, `ingest_batch.py:29`), every one of these
raises `OSError 17` (verified). `enhancements.py:863` catches it, prints "continuing", retries,
and returns `'error'` — so after the migration, **ingest would silently stop placing documents**.

The hardlink design was for KOLMOGOROV: one drive, a large one-time import, and a wish to
reorganize without duplicating 29 GB. That need is gone. The library is a production system
now; the workflow is download → import → delete the download. **Copy.**

---

## 5. The change

### 5.1 [home] Re-point the app home and fail fast when it is not set up

**File:** `src/archivum/__init__.py`

Replace `_get_local_folder` with the idiom used by `fiscus_project`, `fiscus_news`,
`fiscus_market`, `fiscus_simulate`, `great-dashboard`, and `great-servers`:

```python
def _app_home() -> Path:
    """Return the archivum app home.

    ``~/.archivum`` by default; override with ``$ARCHIVUM_HOME``. The directory
    is not created here: the home is a set of symlinks into the data and
    settings trees (see scripts/New-ArchivumHome.ps1) and must exist before
    archivum runs. The override exists so a dev shell can point archivum at a
    scratch tree without disturbing the production home.
    """
    return Path(os.environ.get("ARCHIVUM_HOME", Path.home() / ".archivum")).expanduser()


BASE_DIR = _app_home()
LIBRARIES_DIR = BASE_DIR / "libraries"
GLOBAL_CONFIG_PATH = BASE_DIR / "global-config.yaml"
```

- Drop the `sys.platform` / `XDG_DATA_HOME` branch. `Path.home()` is correct on every platform
  and no sibling branches. `sys` and `os` stay imported (both still used).
- **Drop the `mkdir` calls at import** (`BASE_DIR`, `LIBRARIES_DIR`). Creating an empty home
  is exactly the wrong thing to do at a symlink site.
- **`_load_global_config` no longer creates a default file.** If `GLOBAL_CONFIG_PATH` does not
  exist, raise `FileNotFoundError` with a message naming the path and
  `scripts/New-ArchivumHome.ps1`. Keep the legacy `global_config.yaml` → `global-config.yaml`
  rename. `DEFAULT_GLOBAL_CONFIG` stays as the merge base for *present* files; it is no longer
  a fallback for missing ones.
- Leave `resolve_path` exactly as it is.

**File:** `src/archivum/library.py`, `Library.__init__` after the two `resolve_path` calls
(`library.py:78-83`). Replace the two `mkdir(parents=True, exist_ok=True)` calls on
`doc_store_path` and `text_dir_path` with a check:

```python
# doc_store_lib / full_text_lib are relative by design and reached by symlink
# from the app home. An absent or empty store means the links were never made;
# opening the library anyway would report every document missing and let the
# next shard run start a second, divergent store.
for label, path in (("doc_store_lib", self.doc_store_path),
                    ("full_text_lib", self.text_dir_path)):
    if not path.is_dir() or not any(path.iterdir()):
        raise FileNotFoundError(
            f"{label} resolves to {path}, which is missing or empty. "
            "The store is reached by symlink from the app home; run "
            "scripts/New-ArchivumHome.ps1 before opening the library."
        )
```

Raise, not warn: this is a single-user production system and a genuinely new empty library is
not a case it needs to support. `exports_dir_path.mkdir` stays — that one is legitimately
created on demand.

**Environment variable name:** `ARCHIVUM_HOME`, per the house `<APP>_<THING>` convention.
`dev/projects/00-dev-rig.md` proposes `ARCHIVUM_BASE_DIR` in "Edit A" and its CHANGELOG
snippet; that brief predates the convention and is unimplemented. Rename the references there
in the same commit.

**New test:** `tests/test_app_home.py`

```python
def test_app_home_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ARCHIVUM_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".archivum").mkdir()
    (tmp_path / ".archivum" / "global-config.yaml").write_text("default_library: null\n")
    importlib.reload(archivum)
    assert archivum.BASE_DIR == tmp_path / ".archivum"

def test_app_home_env_override(monkeypatch, tmp_path):
    home = tmp_path / "scratch"
    home.mkdir()
    (home / "global-config.yaml").write_text("default_library: null\n")
    monkeypatch.setenv("ARCHIVUM_HOME", str(home))
    importlib.reload(archivum)
    assert archivum.BASE_DIR == home

def test_missing_global_config_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHIVUM_HOME", str(tmp_path / "nowhere"))
    with pytest.raises(FileNotFoundError):
        importlib.reload(archivum)
```

A module-scoped fixture reloads `archivum` once more at teardown so the rest of the session
sees the real home. Modules that did `from archivum import BASE_DIR` keep their original
binding throughout, which is what we want.

### 5.2 [copy] Replace every `hardlink_to` with a copy

Replace all seven sites listed in §4 with `shutil.copy2(src, dst)`; add `import shutil` where
missing. `copy2` preserves mtime, overwrites an existing destination, and works across volumes.

- `config.py:113-117`, `library.py:1189-1193` — the `.bak` writers. The
  `if bak.exists(): bak.unlink()` dance goes; `copy2` overwrites.
- `enhancements.py:855-866` and `1048-1058` — sharding. The `samefile` short-circuit stays
  (harmless: a copy is never `samefile`, so it recopies — fine, this path runs once per
  document). The `except OSError` retry-with-alternate-path branch was there for hardlink
  quirks; keep the fallback shape but with `copy2`.
- `utilities.py:483` — the `execute` guard stays; only the verb changes.
- `import_bibtex.py:1426, 1524` — audit copies.
- `web/services/ingest_batch.py:131-146` — `_stage_file` already tries hardlink then copies.
  Simplify to copy only and fix its docstring, which says "Hard links are how sharding already
  works here."

Also update prose that promises hardlinks: `CLAUDE.md` "Read this first" ("Sharding uses
hardlinks"), `ingest_batch.py:260` docstring ("Originals are untouched (hard links)" — still
true, differently), and `README.md` if it says so (grep `hardlink|hard link`).

**New test:** `tests/test_config_backup.py` — write a config, save over it with `backup=True`,
assert `config.bak` still holds the *original* text. Cover `Configurator.save()` only;
`write_bibtex` needs a live library and is covered by acceptance check 13.

### 5.3 [wiring] Build the app home — one-time operator procedure

Add `scripts/New-ArchivumHome.ps1`. It is idempotent, refuses to clobber real directories or
files, and derives every path from parameters — no drive letters baked into the repo (house
rule). Defaults name the current DOOB locations.

```powershell
param(
    [string]$AppHome  = (Join-Path $HOME '.archivum'),   # not $Home: PowerShell automatic variable
    [string]$DataRoot = 'D:\archivum',
    [string]$Settings = 'D:\Settings\archivum',
    [string]$Library  = 'uber-library',
    [switch]$Execute            # dry-run unless supplied (house convention)
)
```

The layout it produces:

```
~\.archivum\
  global-config.yaml            → LINK  D:\Settings\archivum\global-config.yaml
  libraries\
    uber-library\               → DIRLINK  D:\archivum\libraries\uber-library\
        config.yaml               → LINK  D:\Settings\archivum\config.yaml
        ref.feather               (real, on D:)
        doc.feather               (real, on D:)
        ref-doc.feather           (real, on D:)
        read.feather              (real, on D:)
        bibtex.bib                (real, on D:, regenerated on first save)
        exports\                  (real, on D:, excluded from backup)
        import-audit\             (real, on D:, excluded from backup)
        semantic-embeddings.feather  (real, on D:, derived, excluded from backup)
  docs\                         → DIRLINK  D:\archivum\ShardedDocLibrary
  full-text\                    → DIRLINK  D:\archivum\ShardedFullText
  models\                       (real, local, derived — ~90 MB, redownloads)
  query_history.txt             (real, local)
```

Steps the script performs:

1. Move the four feathers from `$Settings` to `$DataRoot\libraries\$Library\`
   (copy, verify size and hash, then remove the source).
2. `New-Item -ItemType Directory` for `$AppHome`, `$AppHome\libraries`, `$AppHome\models`.
3. `New-Item -ItemType SymbolicLink` for the two directory links (`docs`, `full-text`), the
   library directory link, `global-config.yaml`, and the nested `config.yaml`.
4. Print the resulting tree with link targets resolved, for eyeballing.

Refusals: any link site occupied by a real (non-reparse-point) file or a non-empty real
directory aborts with the offending path. An existing link with the *same* target is skipped
(idempotence); a link with a different target aborts.

With §5.1 in place, archivum can no longer create anything at a link site by accident — an
import or a `Library()` before wiring raises instead. So the ordering hazard is gone, but the
script still refuses, belt and braces.

Windows symlink creation needs Developer Mode or an elevated shell; Developer Mode is already
on here (verified — symlink creation succeeds unelevated).

### 5.4 [service] Pin the app home for the Windows service

`~/.great-servers/servers.yaml` registers archivum as a WinSW service (`service_name:
archivum`, port 9124, `cwd: V:/dev/archivum`). `Path.home()` resolves **per account**; with
`password_env` unset the service installs as **LocalSystem**, whose home is
`C:\Windows\system32\config\systemprofile`. Under that account archivum would now raise on
startup (no global config) rather than silently serve an empty library — better, but still
down.

`great-servers` supports a per-server `env:` map, emitted into the WinSW XML as
`<env name=... value=.../>` (`winsw.py:90-97`). Add to the archivum entry:

```yaml
    env:
      ARCHIVUM_HOME: C:/Users/steve/.archivum
```

This is a hard-coded user path, allowed because `D:\Settings\great-servers\servers.yaml` is a
machine-local settings file, not a repo. Edit it, then reinstall the service so the XML is
regenerated.

### 5.5 [docs] Update the path references

| file | lines |
|---|---|
| `README.md` | 204, 228, 234; any hardlink prose |
| `docs/data-model.rst` | 46, 52 |
| `CLAUDE.md` | 93, 96 (configuration section) — add `$ARCHIVUM_HOME`; "Sharding uses hardlinks" in Read this first |
| `dev/projects/00-dev-rig.md` | `ARCHIVUM_BASE_DIR` → `ARCHIVUM_HOME` throughout; the `$env:LOCALAPPDATA\archivum` copy recipes at lines 21, 168-169 |
| `dev/projects/11-mendeley-onramp.md` | line 28, the fresh-user default doc-store suggestion |
| `web/templates/help.html` | only if it mentions the home path or hardlinks (grep) |
| `CHANGELOG.md` | new 2.5.0 entry |

Leave `GEMINI.md` and `codex.md` alone — historical session records.

---

## 6. Acceptance checks

Run in order. **Wiring (1–4) comes before any Python import of archivum on this machine**, so
the fail-fast paths are exercised only by the tests, against `tmp_path`.

**[wiring]**

1. `.\scripts\New-ArchivumHome.ps1` (no `-Execute`) prints the intended actions and creates
   nothing; `D:\Settings\archivum` still holds the feathers.
2. After `-Execute`: `Get-ChildItem ~\.archivum -Force | Select Name, LinkTarget` shows
   `docs`, `full-text`, `libraries\uber-library`, and `global-config.yaml` with the expected
   targets. `D:\Settings\archivum` holds only the two YAML files.
3. `(Get-ChildItem ~\.archivum\docs -Recurse -File).Count` → **7,648**;
   `~\.archivum\full-text` → **7,504**.
4. Run the script again with `-Execute`: every step reports "already in place", nothing changes.

**[home]**

5. `uv run --extra test pytest tests/test_app_home.py tests/test_config_backup.py -q` → passes.
6. `uv run python -c "from archivum import BASE_DIR; print(BASE_DIR)"` →
   `C:\Users\steve\.archivum`.
7. With `$env:ARCHIVUM_HOME = 'C:\tmp\ax'` (nonexistent), the same command raises
   `FileNotFoundError` naming the path and the script. Unset again.
8. `uv run archivum uber -a` opens `uber-library` with the expected reference and document
   counts. `~\.archivum` itself, excluding reparse points, is under ~100 MB (models only).

**[copy]**

9. Save a library config twice with different values; `config.bak` holds the *previous*
   content.
10. Ingest one PDF from `C:\Users\steve\Downloads` through the web Ingest page. It lands in
    `D:\archivum\ShardedDocLibrary\...` as a real file; the download is untouched; text is
    extracted into `D:\archivum\ShardedFullText`. Then delete the download and confirm the
    library copy opens.
11. The same via the CLI import path (`import-bibtex` with a `file =` on `C:`), if a suitable
    input is to hand; otherwise note it skipped.

**[watcher — the thing most likely to break]**

12. Start `uv run archivum serve -b`. In a second shell, make a change through the CLI that
    writes `ref.feather`. The web app picks it up without a restart.

**[bibtex]**

13. Trigger a BibTeX write. `D:\Projects\Biblio\uber-library.bib` exists and is a symlink to
    `D:\archivum\libraries\uber-library\bibtex.bib`; `bibtex.bak` beside `bibtex.bib` holds the
    previous projection after a second write; the four `.csl` files are untouched.

**[service]**

14. Reinstall the archivum service; its WinSW XML contains the `ARCHIVUM_HOME` env line.
    `http://127.0.0.1:9124` serves the real library after a service restart.

**[regression]**

15. `.\scripts\Test-ArchivumWeb.ps1 -Mode Fast` → no new failures.
16. `.\scripts\Test-ArchivumWeb.ps1 -Mode Slow` → no new failures. Expect the first semantic
    run to be slow: `models/` and `semantic-embeddings.feather` are both cold.

---

## 7. Rollback

- **[home] and [copy]** are ordinary code changes on `master`; `git revert`.
- **[wiring]** destroys nothing: the real bytes are on `D:` throughout. Remove each link
  individually with `(Get-Item $p).Delete()` — PowerShell's recursive delete has historically
  been inconsistent about following directory links; do not `Remove-Item -Recurse` the home.
- Moving the feathers from `D:\Settings\archivum` to `D:\archivum\libraries\uber-library` is the
  one destructive step. The script copies, verifies, then removes; without `-Execute` it only
  reports.
- `%LOCALAPPDATA%\archivum` does not exist on this machine — nothing to lose.

---

## 8. Definition of done

- [ ] `_app_home()` in place, `$ARCHIVUM_HOME` honored, XDG branch and import-time `mkdir`s gone
- [ ] Missing `global-config.yaml` raises; missing or empty store raises in `Library.__init__`
- [ ] `tests/test_app_home.py` passing
- [ ] All seven `hardlink_to` sites are `shutil.copy2`; `tests/test_config_backup.py` passing
- [ ] `scripts/New-ArchivumHome.ps1` committed, dry-run by default, idempotent
- [ ] `~/.archivum` built; acceptance checks 1–13 pass
- [ ] `ARCHIVUM_HOME` added to the archivum entry in `servers.yaml`; service reinstalled
- [ ] `dev/projects/00-dev-rig.md` renamed to `ARCHIVUM_HOME`
- [ ] README, `docs/data-model.rst`, `CLAUDE.md`, help.html (if applicable) updated
- [ ] `pyproject.toml` → 2.5.0, CHANGELOG entry, one commit per bump
- [ ] Fast and Slow test modes clean

---

## 9. Decisions (closed)

- **`exports/` lands on `D:`.** `library.py:89` puts it under `config_path`, which is a
  directory symlink to `D:`. Accepted; the backup selector excludes `exports/`,
  `import-audit/`, and `semantic-embeddings.feather`. No code change.
- **`doc_store_lib` / `full_text_lib` stay relative** (`docs`, `full-text`), reached by
  symlink, so the config says nothing machine-specific. The guard in §5.1 makes an unlinked
  home an error.
- **`bibtex_file` stays a symlink** into the library, at `D:\Projects\Biblio\uber-library.bib`.
  `D:\Projects` is a synced tree and sync clients may not carry the symlink as content; the
  user does not need the synced copy. `write_bibtex`'s publish step is unchanged.
- **Hardlinks → copies everywhere.** Multi-drive machine, production workflow of download →
  import → delete; the KOLMOGOROV reason for hardlinks (reorganize without duplicating 29 GB)
  no longer applies.
- **Fail fast, don't auto-create.** Single-user system; a missing home or empty store is a
  setup error, not a first run.

## 10. Open questions

None.
