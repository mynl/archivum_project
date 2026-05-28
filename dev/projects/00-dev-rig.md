# Project 00 — Dev rig + safety belts

**Status:** queued
**Branch:** `sqlite-migration` (to be created)
**Worktree:** `T:\archivum-dev\src`
**No SQLite work in this project.** That starts in Project 01.

## Goal

Stand up a fully insulated dev environment on T: drive so all subsequent SQLite migration work can proceed without any risk of touching prod data on C:. Land four small code edits that make the isolation enforceable from inside the code rather than relying on operator memory. Copy the library metadata, the sharded doc store, and the full-text extracts to T:. Prove isolation with a smoke-test checklist. Produce a reusable runbook at `dev/dev-environment.md` so the rig can be rebuilt cold.

This is **the** load-bearing project. Everything that follows assumes this rig works. If any DoD checkbox doesn't tick, do not proceed to Project 01.

## Prereqs

- Prod working tree clean and pushed. `git status` empty, `git push` succeeds.
- T: drive mounted, fast (verify with a `Measure-Command { Copy-Item ... }` on a 1 GB sample if unsure).
- `rclone` installed and on PATH (`rclone version` returns something). Fallback is `Robocopy` (built into Windows) which also works fine — note in the runbook.
- Disk space on T: ≥ size of `doc_store_lib` + `full_text_lib` + library metadata + working room. Check first:
  ```powershell
  $store = (Get-Content "$env:LOCALAPPDATA\archivum\global-config.yaml" | Select-String 'doc_store_lib').ToString()
  # eyeball the path, then:
  Get-ChildItem -Recurse -LiteralPath '<doc_store_path>' | Measure-Object -Sum Length |
    Select-Object @{n='GB'; e={[math]::Round($_.Sum/1GB, 2)}}
  ```
  Cross-device copy expands hardlinks, so plan for the **expanded** size (each hardlink becomes its own file).
- Prod web on port 9124 confirmed running so we have something to A/B against.

## Files to read first

A cold session should open these before touching anything:

- `src/archivum/__init__.py` — `BASE_DIR` is set at import time from `os.environ["LOCALAPPDATA"]`. This is the single point the `ARCHIVUM_BASE_DIR` override edits.
- `src/archivum/config.py` — `Configurator` Pydantic model; `bibtex_file` field; `load_configuration` precedence.
- `src/archivum/library.py` — specifically:
  - `Library.__init__` and `reset()` (lines ~58–105, 240–270) for path resolution.
  - `Library.write_bibtex()` (lines ~1043–1082) for the symlink behaviour that's the riskiest cross-drive trap.
  - `Library.validate()` (lines ~576–751) — touches the doc store.
  - The `LibraryChangeHandler` watchdog (lines ~107–157) — stays for now, deleted in Project 05.
- `src/archivum/enhancements.py` — `save_from_row` (the single function that hardlinks files into `doc_store_path`; ideal place for the READ_ONLY_DOCSTORE guard).
- `src/archivum/web/app.py` — `inject_lib()` context processor; the dev banner relies on `config.name` propagating to templates.
- `src/archivum/web/templates/base.html` (or whichever base template displays `lib.name`) — to confirm the (DEV) suffix renders prominently.
- `CLAUDE.md` repo root — conventions, esp. PowerShell-only + `rg` rules.
- `dev/projects/README.md` — the meta plan and locked decisions.

## Plan of attack

1. Make the worktree on T:.
2. Bootstrap the dev venv.
3. Apply the four safety-belt code edits as **one commit** on the `sqlite-migration` branch.
4. Run the manual setup runbook (copy library, doc store, text extracts; hand-edit configs).
5. Walk the smoke-test checklist.
6. Commit `dev/dev-environment.md` (the lasting runbook artifact, extracted from this brief).

## The four safety-belt code edits

These are deliberately tiny. Combined diff target: under 80 lines including tests. One commit, clear message: `Project 00: dev-rig safety belts`.

### Edit A — `ARCHIVUM_BASE_DIR` env override

**File:** `src/archivum/__init__.py`, `_get_local_folder()`.

**Why:** clobbering `LOCALAPPDATA` for the dev shell would also affect any other Windows program launched from that shell. An explicit, archivum-specific env var documents intent and doesn't leak.

**Sketch:**

```python
def _get_local_folder() -> Path:
    override = os.environ.get("ARCHIVUM_BASE_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"]) / __appname__
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / __appname__
    base.mkdir(parents=True, exist_ok=True)
    return base
```

Note the existing function nests `app_data = base / __appname__` separately; the override version skips that nesting because the dev path already includes the suffix. Make sure to test both branches.

**Test:** new `tests/test_base_dir_override.py` with two cases — env var set vs unset, both produce expected `BASE_DIR`. Use `monkeypatch.setenv` / `monkeypatch.delenv` and `importlib.reload(archivum)`.

### Edit B — `ARCHIVUM_READ_ONLY_DOCSTORE` guard

**File:** `src/archivum/enhancements.py`, top of `save_from_row()`.

**Why:** `save_from_row` is the single function that creates hardlinks in the doc store. Guarding here covers every caller (`Library.validate(execute=True)`, `update_library` → sharding step, `enhance_doc_df(update=True)`) with one check.

**Sketch:**

```python
def save_from_row(row, base_path):
    if os.environ.get("ARCHIVUM_READ_ONLY_DOCSTORE"):
        raise RuntimeError(
            "ARCHIVUM_READ_ONLY_DOCSTORE is set; refusing to create hardlink in "
            f"{base_path}. Unset the env var to allow doc-store mutation."
        )
    # ... existing body ...
```

Belt-and-braces given we're going to copy the doc store anyway, but cheap and unambiguous. Errors must be **loud** — uncaught `RuntimeError`, not a logged warning, not a silent skip.

**Test:** `tests/test_readonly_docstore_guard.py` — `monkeypatch.setenv("ARCHIVUM_READ_ONLY_DOCSTORE", "1")`, call `save_from_row` with a dummy row, expect `RuntimeError`.

### Edit C — Dev banner via `config.name`

**Files:** none if the existing UI already prominently shows `lib.name`. Verify first by visiting `http://127.0.0.1:9124` and reading the header.

**Sketch (only if needed):** `src/archivum/web/templates/base.html` — wrap `{{ lib.name }}` in a span with a CSS class that highlights yellow when the name contains `(DEV)`. Equivalent for the uber prompt in `cli.py:get_prompt` (currently shows `[{lib_name}]`).

**Decision deferred to execution:** if the existing UI is obvious enough with the renamed library (`Uber Library (DEV)`), no code change. If not, add a 5-line CSS rule + class hook. **Do not** add a third env var (`ARCHIVUM_DEV_MODE`) — the config name is enough.

### Edit D — Refuse `bibtex_file` symlink outside `BASE_DIR` (opt-in)

**File:** `src/archivum/library.py`, top of `Library.write_bibtex()`.

**Why:** `write_bibtex` creates a symlink at `self.config.bibtex_file`. In prod, the user often has `bibtex_file` pointing at a synced location outside `BASE_DIR` so other tools can find it — so we can't make this check unconditional. Make it opt-in via env var; set it in the dev shell. Future improvement: per-library `confine_bibtex_to_base: bool` config field.

**Sketch:**

```python
def write_bibtex(self):
    bibtex_path = self.config_path / "bibtex.bib"
    bibtex_path = Path(bibtex_path).absolute()
    # ... existing body that writes bibtex_path ...

    if self.config.bibtex_file:
        p = Path(self.config.bibtex_file).absolute()
        if os.environ.get("ARCHIVUM_CONFINE_BIBTEX"):
            try:
                p.relative_to(BASE_DIR)
            except ValueError:
                raise RuntimeError(
                    f"ARCHIVUM_CONFINE_BIBTEX is set; refusing to symlink bibtex_file "
                    f"{p} outside BASE_DIR {BASE_DIR}."
                )
        # ... existing symlink body ...
```

**Test:** `tests/test_confine_bibtex.py` — with the env var set and a `bibtex_file` outside `BASE_DIR`, calling `write_bibtex` raises. With env var unset, behaviour unchanged.

## The runbook (extract to `dev/dev-environment.md` once executed)

All commands PowerShell 7. Assumes prod library is `uber-library`; rename if yours differs.

```powershell
# --- 1. Worktree ---
# From the prod repo working dir:
cd C:\Users\steve\Documents\CloudStation\TELOS\Python\archivum_project
git fetch origin
git status                # must be clean
git worktree add T:\archivum-dev\src -b sqlite-migration

# --- 2. Bootstrap dev venv ---
cd T:\archivum-dev\src
uv venv .venv
uv sync --extra test

# --- 3. Apply the 4 safety-belt edits (one commit) ---
#   (done as actual code edits in execution; see "The four safety-belt code edits" above)
git add src/archivum/__init__.py src/archivum/enhancements.py src/archivum/library.py tests/
git commit -m "Project 00: dev-rig safety belts"

# --- 4. rclone library metadata ---
$dev = 'T:\archivum-dev\AppData\Local\archivum'
New-Item -ItemType Directory -Force -Path $dev | Out-Null
Copy-Item -LiteralPath "$env:LOCALAPPDATA\archivum\global-config.yaml" -Destination "$dev\global-config.yaml"
rclone copy "$env:LOCALAPPDATA\archivum\libraries\uber-library" "$dev\libraries\uber-library-dev" --progress

# --- 5. rclone doc store and full text ---
# Read the absolute paths from prod's global-config.yaml or the library's config.yaml first.
# Cross-device copy expands hardlinks; that's accepted (no --hard-links flag possible cross-device).
rclone copy '<prod doc_store_path>'   'T:\archivum-dev\sharded-docs' --progress
rclone copy '<prod full_text_path>'   'T:\archivum-dev\full-text'    --progress

# --- 6. Hand-edit the dev configs ---
# T:\archivum-dev\AppData\Local\archivum\global-config.yaml:
#   default_library: uber-library-dev
#   doc_store_lib:   T:\archivum-dev\sharded-docs
#   full_text_lib:   T:\archivum-dev\full-text
#
# T:\archivum-dev\AppData\Local\archivum\libraries\uber-library-dev\config.yaml:
#   name:          "Uber Library (DEV)"
#   bibtex_file:   T:\archivum-dev\AppData\Local\archivum\libraries\uber-library-dev\bibtex.bib
#   doc_store_lib, full_text_lib: same as global (or omit if same)
#   debug_dir:     T:\archivum-dev\AppData\Local\archivum\debug

# --- 7. Dev shell session env ---
$env:ARCHIVUM_BASE_DIR           = $dev
$env:ARCHIVUM_LIBRARY            = 'uber-library-dev'
$env:ARCHIVUM_READ_ONLY_DOCSTORE = '1'      # belt-and-braces — docs are copied so this is defence-in-depth
$env:ARCHIVUM_CONFINE_BIBTEX     = '1'
# Optionally persist to a .ps1 you can dot-source: dev-shell.ps1

# --- 8. Sanity check the override ---
uv run python -c "from archivum import BASE_DIR, LIBRARIES_DIR, DEFAULT_LIBRARY; print(BASE_DIR); print(LIBRARIES_DIR); print(DEFAULT_LIBRARY)"
# expect:
#   T:\archivum-dev\AppData\Local\archivum
#   T:\archivum-dev\AppData\Local\archivum\libraries
#   uber-library-dev

# --- 9. Launch dev web ---
uv run archivum serve -b --port 9125
# Browse http://127.0.0.1:9125 ; header should say "(DEV)".
```

## Acceptance tests — DoD checklist

Every box ticked before proceeding to Project 01.

### Code

- [ ] Branch `sqlite-migration` exists on origin.
- [ ] Worktree at `T:\archivum-dev\src`, `uv sync --extra test` succeeds in it.
- [ ] First commit on the branch contains exactly the 4 safety-belt edits + their unit tests + `dev/dev-environment.md`. Nothing else.
- [ ] `uv run --extra test pytest tests/test_base_dir_override.py tests/test_readonly_docstore_guard.py tests/test_confine_bibtex.py -q` passes in the dev venv.
- [ ] `.\scripts\Test-ArchivumWeb.ps1 -Mode Fast` passes in the dev venv against the dev library.

### Isolation (the load-bearing checks)

- [ ] **Override works in dev:** in the dev shell, `uv run python -c "from archivum import BASE_DIR; print(BASE_DIR)"` prints `T:\...`.
- [ ] **Prod untouched:** in a fresh prod shell (no env vars set), the same command prints `C:\Users\steve\AppData\Local\archivum`.
- [ ] **Docstore guard fires:** in the dev shell, `uv run archivum library-validate --task sharding -x` raises `RuntimeError` loudly because `ARCHIVUM_READ_ONLY_DOCSTORE=1`.
- [ ] **Bibtex confinement fires (if Edit D enabled):** temporarily set `bibtex_file` in the dev config to a `C:\...` path; `uv run archivum library-save` raises. Restore the dev config afterwards.

### Web parity + cross-contamination

- [ ] Dev web on port 9125 loads. Header shows "(DEV)".
- [ ] Edit a tag in dev. `T:\...\uber-library-dev\bibtex.bib` mtime advances. `C:\...\uber-library\bibtex.bib` mtime does **not**. (Capture before/after with `Get-Item ... | Select-Object FullName, LastWriteTime`.)
- [ ] Open a PDF in dev. `T:\...\uber-library-dev\read.feather` mtime advances. `C:\...\uber-library\read.feather` mtime does **not**.
- [ ] With prod web running on port 9124 *and* dev web running on port 9125, edit a tag in **prod**. Prod bibtex mtime advances. Dev bibtex mtime does **not**.
- [ ] Both web instances reachable simultaneously, no log errors related to file locks or cross-process conflicts.

### Runbook

- [ ] `dev/dev-environment.md` exists, committed, and is a literal cold-recreation runbook (someone other than the author could follow it). Include: prereqs, every command verbatim, the hand-edit instructions, the smoke-test checklist.

## Rollback

If anything goes wrong before this branch is merged:

1. `git worktree remove T:\archivum-dev\src` (from the prod repo). Worktree gone.
2. `git branch -D sqlite-migration` if you're sure.
3. Delete `T:\archivum-dev\` if you want the disk space back.
4. Prod on C: never received any of this; nothing to revert there.

If the safety-belt commit needs to be amended after the worktree is created: just `git commit --amend` in the worktree before pushing. Never amend after push if anyone else has pulled (no one will have, but discipline).

## PR description draft

```
Project 00: dev rig + safety belts

Adds four small safety-belt code changes that make a fully insulated dev environment
on T: enforceable from inside the code, plus the runbook for setting up that
environment.

- ARCHIVUM_BASE_DIR env var overrides the default %LOCALAPPDATA%\archivum location.
  Lets a dev shell point archivum at T: without clobbering LOCALAPPDATA for other apps.
- ARCHIVUM_READ_ONLY_DOCSTORE guard in save_from_row() — single chokepoint for all
  hardlink creation in the doc store. When set, raises loudly; covers Library.validate,
  the importer's sharding step, and enhance_doc_df.
- ARCHIVUM_CONFINE_BIBTEX guard in Library.write_bibtex() — opt-in check that
  bibtex_file is under BASE_DIR. Prevents the dev library from accidentally hijacking
  prod's bibtex symlink.
- Dev banner: relies on config.name containing "(DEV)" rendering via the existing
  inject_lib context processor. No template change needed.

Also lands dev/dev-environment.md — the cold-recreation runbook for the T: rig.

No SQLite work in this PR; this is purely infrastructure for the migration work that
follows (see dev/projects/README.md for the full plan).

Smoke tests in DoD all pass on T: against a copy of uber-library.
```

## Notes for the executing session

- The four edits are small but each needs a unit test. Don't merge without them — they're the only things proving the env vars actually do anything.
- Edit C (dev banner) requires inspecting the live UI before deciding whether code needs to change. Don't assume; visit the running prod web and look.
- The rclone steps are the slow part of the runbook (hours, depending on doc store size). Run them in the background while doing the code edits.
- After the rclone is done, **before** running the dev web, double-check the hand-edited configs with `Get-Content $dev\global-config.yaml` and `Get-Content $dev\libraries\uber-library-dev\config.yaml` and visually confirm every path points at T:.
- If you find any other module-level path constants while reading the source files, flag them — `_get_local_folder` is the one I know about but I haven't audited every file.
