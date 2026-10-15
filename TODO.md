# TODO

Small deferred items to pick up next time the relevant area is being changed.
Larger multi-session work belongs in `dev/projects/`; shipped changes go in
`CHANGELOG.md`.

## Open

## Done

<!-- Move items here with the version they shipped in, or delete them. -->

- 2.6.0: the version is surfaced: `archivum --version` and the web About page
  ([menu-about], `dev/plan-web-usability-pass.md`). `uv run` rebuilds the
  editable install when `pyproject.toml` changes, so it reports a bump at once;
  only a server started straight from `.venv\Scripts` lags until the next
  `uv run` or `uv sync`.
- 2.5.4: Up/Down in the Edit page's BibTeX box move within the entry, with an
  unsaved-change guard ([edit-keys], `dev/plan-web-usability-pass.md`).
