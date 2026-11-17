"""``Library.write_bibtex`` replaces ``bibtex.bib`` whole or leaves it alone; writes hit a temp library."""

from __future__ import annotations

import os

import pytest

from archivum.bibtex import count_bibtex_entries

REFS = [
    {"tag": "Keep2020", "type": "article", "title": "One Work", "author": "Doe, Jane", "year": "2020",
     "journal": "J. One", "doi": "10.1/one"},
    {"tag": "Other2019", "type": "book", "title": "Other", "author": "Roe, R.", "year": "2019",
     "journal": "", "doi": ""},
]


@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


def _bib(lib):
    return lib.config_path / "bibtex.bib"


def _retitle(lib, title):
    lib.ref_df.loc[lib.ref_df["tag"] == "Keep2020", "title"] = title


def test_write_replaces_the_file_and_leaves_no_temporary(lib):
    _retitle(lib, "One Work, Revised")
    lib.write_bibtex()

    text = _bib(lib).read_text(encoding="utf-8")
    assert "One Work, Revised" in text and count_bibtex_entries(text) == 2
    assert not _bib(lib).with_name("bibtex.bib.tmp").exists()
    assert not _bib(lib).with_suffix(".bak").exists()


def test_a_write_that_fails_part_way_leaves_the_old_file_byte_identical(lib, monkeypatch):
    before = _bib(lib).read_bytes()
    _retitle(lib, "One Work, Revised")

    def fail(fd):
        raise OSError("disk full")

    monkeypatch.setattr("archivum.library.os.fsync", fail)
    with pytest.raises(OSError, match="disk full"):
        lib.write_bibtex()

    assert _bib(lib).read_bytes() == before


def test_a_text_with_too_few_entries_is_refused(lib, monkeypatch):
    before = _bib(lib).read_bytes()
    monkeypatch.setattr("archivum.library.rows_to_bibtex",
                        lambda rows, allowed_fields=None: "@article{Keep2020,\n  title = {One},\n}")

    with pytest.raises(ValueError, match="1 entries built for 2 references"):
        lib.write_bibtex()

    assert _bib(lib).read_bytes() == before
    assert not _bib(lib).with_name("bibtex.bib.tmp").exists()


def test_a_held_file_is_retried_then_raises_with_the_old_file_intact(lib, monkeypatch):
    before = _bib(lib).read_bytes()
    _retitle(lib, "One Work, Revised")
    calls = []

    def held(src, dst):
        calls.append(src)
        raise PermissionError("in use")

    monkeypatch.setattr("archivum.library.os.replace", held)
    monkeypatch.setattr("archivum.library.BIBTEX_REPLACE_WAIT_SECONDS", 0)
    with pytest.raises(PermissionError):
        lib.write_bibtex()

    assert len(calls) == 5
    assert _bib(lib).read_bytes() == before
    assert "One Work, Revised" in _bib(lib).with_name("bibtex.bib.tmp").read_text(encoding="utf-8")


def test_a_brief_hold_is_ridden_out(lib, monkeypatch):
    _retitle(lib, "One Work, Revised")
    real_replace, calls = os.replace, []

    def held_once(src, dst):
        calls.append(src)
        if len(calls) == 1:
            raise PermissionError("in use")
        real_replace(src, dst)

    monkeypatch.setattr("archivum.library.os.replace", held_once)
    monkeypatch.setattr("archivum.library.BIBTEX_REPLACE_WAIT_SECONDS", 0)
    lib.write_bibtex()

    assert len(calls) == 2 and "One Work, Revised" in _bib(lib).read_text(encoding="utf-8")


def test_the_replaced_file_is_backed_up_and_ten_backups_are_kept(lib):
    backups = lib.config_path / "backups"
    for n in range(12):
        (backups / f"bibtex_20200101_0000{n:02d}.bib").write_text("old", encoding="utf-8")
    before = _bib(lib).read_bytes()
    _retitle(lib, "One Work, Revised")
    lib.write_bibtex()

    kept = sorted(backups.glob("bibtex_*.bib"))
    assert len(kept) == 10
    assert kept[-1].read_bytes() == before
    assert kept[0].name == "bibtex_20200101_000003.bib"


def test_a_symlink_to_the_file_survives_the_replace(lib, tmp_path):
    link = tmp_path / "linked.bib"
    try:
        link.symlink_to(_bib(lib))
    except OSError:
        pytest.skip("symlinks are not available to this user")
    lib.config = lib.config.model_copy(update={"bibtex_file": str(link)})
    _retitle(lib, "One Work, Revised")
    lib.write_bibtex()

    assert link.is_symlink()
    assert "One Work, Revised" in link.read_text(encoding="utf-8")


def test_an_at_sign_starting_a_line_in_a_field_is_not_counted():
    text = "@article{A2020,\n  abstract = {first line\n@ second line},\n}\n\n@book{B2019,\n  title = {T},\n}"
    assert count_bibtex_entries(text) == 2
    assert count_bibtex_entries("") == 0
