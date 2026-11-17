"""``Library.apply_field_changes`` and ``doi-audit-revert``; every write hits a temp library."""

from __future__ import annotations

import pandas as pd
import pytest
from click.testing import CliRunner

from archivum.doi_audit import changesets as cs

REFS = [
    {"tag": "Keep2020", "type": "article", "title": "One Work", "author": "Doe, Jane", "year": "2020",
     "journal": "J. One", "doi": "https://doi.org/10.1/one"},
    {"tag": "Other2019", "type": "book", "title": "Other", "author": "Roe, R.", "year": "2019",
     "journal": "", "doi": None},
    {"tag": "Third2018", "type": "misc", "title": "Third", "author": "Poe, P.", "year": "2018",
     "journal": None, "doi": "doi:10.1/three"},
]


@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS)
    library.save()
    return library


def _disk(lib):
    return pd.read_feather(lib.config_path / "ref.feather")


def _changesets(lib):
    return [cs.read_changeset(p) for p in sorted(cs.changeset_dir(lib.config_path).glob("*.json"))]


def test_a_commit_changes_exactly_the_listed_cells(lib):
    before = _disk(lib)
    path = lib.apply_field_changes(
        [("Keep2020", "doi", "https://doi.org/10.1/one", "10.1/one"), ("Third2018", "doi", "doi:10.1/three", "10.1/three")],
        "normalize",
    )
    after = _disk(lib)

    assert after.loc[0, "doi"] == "10.1/one" and after.loc[2, "doi"] == "10.1/three"
    differing = (before != after) & ~(before.isna() & after.isna())
    assert int(differing.to_numpy().sum()) == 2
    for column in ("tag", "author", "year"):
        assert before[column].equals(after[column])
    assert list(before.columns) == list(after.columns) and before.dtypes.equals(after.dtypes)
    assert "10.1/one" in (lib.config_path / "bibtex.bib").read_text(encoding="utf-8")

    changeset = cs.read_changeset(path)
    assert path.parent == lib.config_path / "doi-audit" / "changesets"
    assert changeset["outcome"] == cs.APPLIED
    assert [r["outcome"] for r in changeset["rows"]] == [cs.APPLIED, cs.APPLIED]
    assert changeset["rows"][0]["old"] == "https://doi.org/10.1/one"


def test_a_row_whose_value_moved_since_review_is_skipped_and_reported(lib):
    path = lib.apply_field_changes(
        [("Keep2020", "doi", "10.1/stale", "10.1/one"), ("Third2018", "doi", "doi:10.1/three", "10.1/three")],
        "normalize",
    )
    after, rows = _disk(lib), cs.read_changeset(path)["rows"]

    assert after.loc[0, "doi"] == "https://doi.org/10.1/one" and after.loc[2, "doi"] == "10.1/three"
    assert (rows[0]["outcome"], rows[0]["note"]) == (cs.SKIPPED, cs.CHANGED_SINCE_REVIEW)
    assert rows[1]["outcome"] == cs.APPLIED


def test_a_batch_where_every_row_is_stale_saves_nothing(lib):
    stamp = (lib.config_path / "ref.feather").stat().st_mtime_ns
    path = lib.apply_field_changes([("Keep2020", "doi", "10.1/stale", "10.1/one")], "normalize")

    assert cs.read_changeset(path)["outcome"] == cs.SKIPPED
    assert (lib.config_path / "ref.feather").stat().st_mtime_ns == stamp


def test_missing_and_empty_are_told_apart(lib):
    lib.apply_field_changes([("Other2019", "doi", None, "10.1/other")], "add")
    assert _disk(lib).loc[1, "doi"] == "10.1/other"

    path = lib.apply_field_changes([("Other2019", "journal", None, "J. Two")], "journal")
    assert cs.read_changeset(path)["rows"][0]["outcome"] == cs.SKIPPED


def test_a_forced_postcondition_failure_saves_nothing(lib, monkeypatch):
    before = (lib.config_path / "ref.feather").read_bytes()
    real = type(lib)._set_ref_cells

    def tamper(frame, cells):
        frame = real(frame, cells)
        frame.iloc[1, frame.columns.get_loc("title")] = "Tampered"
        return frame

    monkeypatch.setattr(type(lib), "_set_ref_cells", staticmethod(tamper))
    with pytest.raises(RuntimeError, match="outside the batch"):
        lib.apply_field_changes([("Keep2020", "doi", "https://doi.org/10.1/one", "10.1/one")], "normalize")

    assert (lib.config_path / "ref.feather").read_bytes() == before
    assert lib.ref_df.loc[1, "title"] == "Other"
    changeset = _changesets(lib)[-1]
    assert changeset["outcome"] == cs.FAILED and "RuntimeError" in changeset["error"]
    assert changeset["rows"][0]["outcome"] == cs.FAILED


@pytest.mark.parametrize("field, new", [("tag", "Keep2021"), ("author", "Doe, John"), ("year", "2021"), ("title", "X")])
def test_tag_author_and_year_are_refused(lib, field, new):
    before = (lib.config_path / "ref.feather").read_bytes()
    with pytest.raises(ValueError, match="not one the commit path writes"):
        lib.apply_field_changes([("Keep2020", field, lib.ref_df.loc[0, field], new)], "bad")

    assert (lib.config_path / "ref.feather").read_bytes() == before
    assert _changesets(lib) == []


def test_one_refused_row_refuses_the_whole_batch(lib):
    with pytest.raises(ValueError, match="Keep2020.year"):
        lib.apply_field_changes(
            [("Third2018", "doi", "doi:10.1/three", "10.1/three"), ("Keep2020", "year", "2020", "2021")], "mixed")

    assert _disk(lib).loc[2, "doi"] == "doi:10.1/three"


@pytest.mark.parametrize("changes, message", [
    ([("Other2019", "type", "book", "article")], "type of a book is never changed"),
    ([("Keep2020", "type", "article", "pamphlet")], "not an entry type"),
    ([("Nobody1900", "doi", None, "10.1/x")], "on 0 references"),
    ([("Keep2020", "journal", "J. One", "J. One")], "old and new values are the same"),
    ([("Keep2020", "journal", "J. One", "A"), ("Keep2020", "journal", "J. One", "B")], "more than once"),
    ([("Keep2020", "pages", None, "1--2")], "no such column"),
])
def test_refusals(lib, changes, message):
    with pytest.raises(ValueError, match=message):
        lib.apply_field_changes(changes, "bad")
    assert _changesets(lib) == []


def test_a_type_change_that_is_not_from_a_book_applies(lib):
    lib.apply_field_changes([("Third2018", "type", "misc", "article")], "type")
    assert _disk(lib).loc[2, "type"] == "article"


def test_a_stale_frame_is_reloaded_before_the_commit(lib):
    disk = _disk(lib)
    disk.loc[0, "title"] = "Edited Elsewhere"
    disk.to_feather(lib.config_path / "ref.feather")
    lib.ref_df  # the in-memory frame, loaded before the other process wrote
    lib.needs_reload = True

    lib.apply_field_changes([("Third2018", "doi", "doi:10.1/three", "10.1/three")], "normalize")

    assert _disk(lib).loc[0, "title"] == "Edited Elsewhere"


def test_revert_restores_every_changed_cell(lib):
    before = _disk(lib)
    path = lib.apply_field_changes(
        [("Keep2020", "doi", "https://doi.org/10.1/one", "10.1/one"), ("Other2019", "doi", None, "10.1/other"),
         ("Other2019", "journal", "", "J. Two")],
        "mixed",
    )
    inverse = cs.inverse_changes(cs.read_changeset(path))
    revert = lib.apply_field_changes(inverse, f"revert-{path.stem}", reverts=path.name)
    after = _disk(lib)

    assert before.equals(after)
    assert pd.isna(after.loc[1, "doi"]) and after.loc[1, "journal"] == ""
    assert cs.read_changeset(revert)["reverts"] == path.name
    assert len(_changesets(lib)) == 2


def test_revert_leaves_a_cell_edited_after_the_commit(lib):
    path = lib.apply_field_changes(
        [("Keep2020", "doi", "https://doi.org/10.1/one", "10.1/one"), ("Third2018", "doi", "doi:10.1/three", "10.1/three")],
        "normalize",
    )
    lib.apply_field_changes([("Keep2020", "doi", "10.1/one", "10.1/one-corrected")], "hand")
    revert = lib.apply_field_changes(cs.inverse_changes(cs.read_changeset(path)), "revert", reverts=path.name)
    after = _disk(lib)

    assert after.loc[0, "doi"] == "10.1/one-corrected" and after.loc[2, "doi"] == "doi:10.1/three"
    assert [r["outcome"] for r in cs.read_changeset(revert)["rows"]] == [cs.SKIPPED, cs.APPLIED]


def test_a_revert_may_restore_a_book(lib):
    path = lib.apply_field_changes([("Third2018", "type", "misc", "book")], "to-book")
    lib.apply_field_changes(cs.inverse_changes(cs.read_changeset(path)), "revert", reverts=path.name)
    assert _disk(lib).loc[2, "type"] == "misc"


def test_two_changesets_in_one_second_get_two_files(lib):
    first = lib.apply_field_changes([("Keep2020", "journal", "J. One", "J. A")], "same")
    second = lib.apply_field_changes([("Keep2020", "journal", "J. A", "J. B")], "same")
    assert first != second and first.exists() and second.exists()


@pytest.fixture()
def cli(lib, monkeypatch, capsys):
    """Run ``doi-audit-revert``; return the exit code and everything it printed."""
    from archivum import cli as cli_module

    monkeypatch.setattr(cli_module.LibraryContext, "get", classmethod(lambda cls: lib))

    def run(*args):
        # the table printer writes through its own stream, so the runner's
        # capture does not hold every line; take both
        result = CliRunner().invoke(cli_module.doi_audit_revert, list(args))
        return result.exit_code, result.output + capsys.readouterr().out

    return run


def test_the_revert_command_is_a_dry_run_without_x(lib, cli):
    path = lib.apply_field_changes([("Keep2020", "doi", "https://doi.org/10.1/one", "10.1/one")], "normalize")

    code, text = cli(path.stem)
    assert code == 0 and "DRY RUN: 1 cells" in text
    assert _disk(lib).loc[0, "doi"] == "10.1/one"

    code, text = cli(path.name, "-x")
    assert code == 0 and "Reverted 1 cells" in text
    assert _disk(lib).loc[0, "doi"] == "https://doi.org/10.1/one"


def test_the_revert_command_lists_changesets_and_reports_an_unknown_one(lib, cli):
    assert "No changesets." in cli()[1]
    path = lib.apply_field_changes([("Keep2020", "doi", "https://doi.org/10.1/one", "10.1/one")], "normalize")

    assert path.stem in cli()[1]
    assert "No changeset 'nothing'" in cli("nothing")[1]
