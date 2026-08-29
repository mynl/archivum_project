"""
App-home resolution: ``~/.archivum`` by default, ``$ARCHIVUM_HOME`` override,
and a hard failure when the home has not been wired up.

These tests reload the ``archivum`` package against a tmp home. The module is
reloaded once more at teardown so the rest of the session sees the real home.
Modules that did ``from archivum import BASE_DIR`` keep their original binding
throughout, which is what we want.
"""
import importlib
from pathlib import Path

import pytest

import archivum


@pytest.fixture(autouse=True)
def restore_archivum(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(archivum)


def _seed_home(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "global-config.yaml").write_text("default_library: null\n", encoding="utf-8")


def test_app_home_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ARCHIVUM_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _seed_home(tmp_path / ".archivum")
    importlib.reload(archivum)
    assert archivum.BASE_DIR == tmp_path / ".archivum"
    assert archivum.LIBRARIES_DIR == tmp_path / ".archivum" / "libraries"


def test_app_home_env_override(monkeypatch, tmp_path):
    home = tmp_path / "scratch"
    _seed_home(home)
    monkeypatch.setenv("ARCHIVUM_HOME", str(home))
    importlib.reload(archivum)
    assert archivum.BASE_DIR == home


def test_missing_global_config_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHIVUM_HOME", str(tmp_path / "nowhere"))
    with pytest.raises(FileNotFoundError, match="New-ArchivumHome"):
        importlib.reload(archivum)
    # Import must not have planted anything at the missing home.
    assert not (tmp_path / "nowhere").exists()
