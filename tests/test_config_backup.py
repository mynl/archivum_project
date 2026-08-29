"""
``Configurator.save(backup=True)`` must leave the *previous* config in
``config.bak``. It used to hardlink, which shares the inode that the
subsequent ``open("w")`` truncates in place -- so the "backup" was rewritten
along with the original.
"""
from archivum.config import Configurator, load_configuration


def _make(lib_dir, description):
    return Configurator(
        name="backup-test",
        description=description,
        bibtex_file=str(lib_dir / "out.bib"),
        debug_mode=False,
        default_library="backup-test",
        debug_dir=lib_dir / "debug",
        doc_store_lib="docs",
        full_text_lib="full-text",
        theme="system",
    )


def test_config_bak_holds_previous_contents(tmp_path):
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    cfg = lib_dir / "config.yaml"
    bak = lib_dir / "config.bak"

    _make(lib_dir, "first").save(lib_dir, backup=True)
    assert cfg.exists() and not bak.exists()
    first_text = cfg.read_text(encoding="utf-8")

    _make(lib_dir, "second").save(lib_dir, backup=True)
    assert bak.read_text(encoding="utf-8") == first_text
    assert "second" in cfg.read_text(encoding="utf-8")
    assert "second" not in bak.read_text(encoding="utf-8")

    # A third save overwrites the backup with the second version.
    _make(lib_dir, "third").save(lib_dir, backup=True)
    assert "second" in bak.read_text(encoding="utf-8")
    assert load_configuration(lib_dir).description == "third"
