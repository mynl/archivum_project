# TODO

Small deferred items to pick up next time the relevant area is being changed.
Larger multi-session work belongs in `dev/projects/`; shipped changes go in
`CHANGELOG.md`.

## Open

- [import-bibtex-vv] `archivum import-bibtex <dir> -vv` crashes on a clean import with
  `ValueError: invalid width np.int64(0)` inside greater_tables. The analysis table's
  `match tag` column is all empty strings when no entry collides with the library, and
  greater_tables 5.2.1 (unpinned in `pyproject.toml`, held at 5.2.1 by `uv.lock`) fails on a
  column with no visible content; 8.1.0 prints it. Fix by upgrading
  (`uv lock --upgrade-package greater_tables`, then check every `qd` table and the web pages
  against the 8.x API) or, smaller, by printing `-` for an empty match tag. Hit 2026-11-05
  through the `add-missing-references` skill, whose preview prints the analysis with pandas.

## Done

<!-- Move items here with the version they shipped in, or delete them. -->

- 2.6.0: the version is surfaced: `archivum --version` and the web About page
  ([menu-about], `dev/plan-web-usability-pass.md`). `uv run` rebuilds the
  editable install when `pyproject.toml` changes, so it reports a bump at once;
  only a server started straight from `.venv\Scripts` lags until the next
  `uv run` or `uv sync`.
- 2.5.4: Up/Down in the Edit page's BibTeX box move within the entry, with an
  unsaved-change guard ([edit-keys], `dev/plan-web-usability-pass.md`).
