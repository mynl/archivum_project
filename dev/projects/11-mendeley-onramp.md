# Project 11 — Mendeley onramp + definitive_loader refactor  (STUB)

**Status:** stub — convert to full brief immediately before execution.
**Prereqs:** Project 07 (or 08).

## One-line goal

Ship `archivum init-from-mendeley <bib> [--docs <dir>]` zero-config command. Refactor `definitive_loader.py` patterns (Trie author-name extension, errors_mapper structure, dedup-by-DOI-then-title) into reusable importer helpers in `import_bibtex.py`. Drop hardcoded user-specific paths. Open-source-friendly README and quickstart.

## Decisions already locked

- `archivum init-from-mendeley` creates the library dir, writes a sensible default `config.yaml`, runs `Bib2df_Incremental`, sets the library as `default_library` if none exists.
- Refactor extracts these patterns from `definitive_loader.py`: errors_mapper structure, dedup-by-DOI-then-title pipeline, Trie-based author name extension.
- Generic loader takes paths as parameters; no hardcoded user paths.
- The user explicitly wants to "offer it out there" — implies PyPI publication is in scope here.

## Files to read first

- `src/archivum/definitive_loader.py` — the patterns to generalize. Hardcoded paths to remove: `C:\\S\\PDFs\\original-sources`, `C:\\S\\PDFs\\new-sources\\`, `C:\\S\\AppData\\archivum\\docs`.
- `src/archivum/import_bibtex.py` — where the refactored helpers land.
- `src/archivum/trie.py` — Trie author-name extension implementation.
- `src/archivum/library.py:initial_import_bibtex_file` — existing entry point.
- `README.md` and `docs/quickstart.rst` — what to update for onboarding.

## Open design questions (resolve when writing the brief)

- PyPI publication checklist: package metadata, `uv build`, `uv publish`, test install in a fresh venv. Worth its own subsection.
- Default `config.yaml` choices for a fresh user: where to put doc store (`~/.archivum/docs`?), what extractor (`pdftotext` if installed else `pymupdf`?), empty `tag_name_mapper`.
- Onboarding docs: a new `docs/onboarding.rst` (this IS Sphinx, user-facing) plus README update. One of the few user-facing additions to `docs/`.
- Zotero export format: probably similar `file = {…}` syntax — minor extension to the vfile parser. Confirm whether worth bundling.
- Should the onramp ask if the user wants to fetch citation counts (Project 10)? Probably yes — a single prompt for a one-shot bulk fetch on first run.

## Risks / gotchas

- `definitive_loader.py` has user-specific `errors_mapper` entries that should stay user-overridable but NOT shipped as defaults. Move to user-supplied YAML/JSON file.
- Trie name extension assumes a library of names to compare against; for a fresh user, build from the import itself (Bib2df_Incremental already does this — verify).
- A first-run user has no global config, no library, no doc store. The command must handle "everything is empty" gracefully.
- Don't break the existing `initial_import_bibtex_file` path; the user's own onboarding used it and is now stable.

## Likely drift sources (revisit this stub after)

- OpenAlex citation integration (Project 10) could plug into the onboarding flow as an optional bulk-fetch.
- Web UI for shelves (Project 09) — onboarding docs should mention them.
