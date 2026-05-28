# Project 02 — `library-clone` CLI  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 00 done.

## One-line goal

Resurrect `Library.copy_library` and wrap it in a CLI `archivum library-clone <src> <dest> [--share-docstore | --copy-docstore]` that formalizes the manual setup from Project 00 (rename library, repoint `bibtex_file`, optionally copy or share doc store + full text).

## Decisions already locked

- CLI: `archivum library-clone <src> <dest>` with mutually-exclusive `--share-docstore` (default — both libraries point at the same `doc_store_lib` and `full_text_lib`) and `--copy-docstore` (rclones the doc store + full text to a fresh location alongside the new library).
- Clone rewrites the destination library's `config.yaml`: `name` (auto-suffix " (DEV)" if dest ends `-dev`), `bibtex_file` (forced inside dest library dir), and `doc_store_lib`/`full_text_lib` if `--copy-docstore`.
- Dest library name validation: error if it already exists.
- Includes `exports/` in the clone (so existing reports are reusable). Excludes `import-audit/`, `enhance-audit/`, `backups/` (these are getting retired post-Project 08 anyway).

## Files to read first

- `src/archivum/library.py` lines ~887–907 (`rename_library`) and ~909–930 (`copy_library` — currently `"UNTESTED"`).
- `src/archivum/cli.py` lines ~649–675 (`library_rename` / `library_copy` CLI commands — both disabled with `"UNTESTED - sorry, not doing that..."`).
- `src/archivum/config.py` (Configurator).
- `dev/dev-environment.md` once Project 00 lands — the manual recipe to formalize.

## Open design questions (resolve when writing the brief)

- Should `library-clone` also copy `embeddings.db` (post-Project 01) and `archivum.db` (post-Project 03)? Probably yes. The cleanest way: a small per-library manifest of "files that belong to this library." Defer.
- Cross-drive clone (C: → T: → external): should work the same way. Test on dev rig.
- For `--copy-docstore`: do we shell out to `rclone` (faster, progress bar) or use `shutil.copytree` (no extra dep)? rclone is already a prereq from Project 00.

## Risks / gotchas

- `Library.copy_library` and `rename_library` exist but their CLI wrappers are explicitly disabled. Anything we ship needs real test coverage on the dev rig before being callable.
- The hand-edited `config.yaml` produced during Project 00 is the reference for what fields need rewriting on clone. Don't drift from it.

## Likely drift sources (revisit this stub after)

- **After Project 01:** add `embeddings.db` to the cloned-file set.
- **After Project 03:** add `archivum.db` to the cloned-file set.
- **After Project 04:** clone may need backend-aware logic (don't copy feathers if backend is sqlite, etc.).
