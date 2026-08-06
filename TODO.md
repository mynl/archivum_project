# TODO

Small deferred items to pick up next time the relevant area is being changed.
Larger multi-session work belongs in `dev/projects/`; shipped changes go in
`CHANGELOG.md`.

## Open

1. **App reports version number somewhere.** `archivum.__version__`
   (`src/archivum/__init__.py:17`) exists but nothing surfaces it. Add
   `@click.version_option(__version__)` to the CLI group in `cli.py` so
   `archivum --version` works, and show it in the web UI (footer in
   `base.html`, or the status page). Do both in one change so the CLI and web
   cannot disagree.

   Note: `__version__` reads *installed* package metadata, so after bumping
   `pyproject.toml` it lags until `uv sync` refreshes the editable install.
   Worth deciding whether that is acceptable or whether it should read
   `pyproject.toml` directly in a dev checkout.

## Done

<!-- Move items here with the version they shipped in, or delete them. -->
