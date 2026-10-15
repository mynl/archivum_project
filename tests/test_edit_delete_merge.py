"""Delete and merge references from Edit ([edit-delete-merge]); writes hit a temp library."""

from __future__ import annotations

import pandas as pd
import pytest

from archivum.bibtex import propose_merge

TZ = "Europe/London"
H1, H2, H3 = ("a1" + "0" * 62, "b2" + "0" * 62, "c3" + "0" * 62)

REFS = [
    {"tag": "Keep2020", "type": "article", "title": "{One Work}", "author": "Doe, Jane",
     "year": "2020", "journal": "", "doi": "10.1/x", "added": pd.Timestamp("2024-05-05", tz=TZ)},
    {"tag": "Dupe2020", "type": "article", "title": "One  Work", "author": "Doe, J.",
     "year": "2020", "journal": "J. Works", "doi": "", "added": pd.Timestamp("2022-02-02", tz=TZ)},
    {"tag": "Bystander2019", "type": "book", "title": "Other", "author": "Roe, R.",
     "year": "2019", "journal": "", "doi": "", "added": pd.Timestamp("2023-03-03", tz=TZ)},
]
DOCS = [{"hash": h, "version": 0, "path": f"x/{h[:2]}.pdf", "name": f"{h[:2]}.pdf", "suffix": ".pdf",
         "size": 1000, "create": pd.Timestamp("2020-01-01", tz=TZ)} for h in (H1, H2, H3)]
# Keep: H1 primary. Dupe: H2 primary, H1 alternate (shared with Keep). Bystander: H3.
LINKS = [
    {"tag": "Keep2020", "hash": H1, "version": 0, "priority": 0},
    {"tag": "Dupe2020", "hash": H2, "version": 0, "priority": 0},
    {"tag": "Dupe2020", "hash": H1, "version": 0, "priority": 1},
    {"tag": "Bystander2019", "hash": H3, "version": 0, "priority": 0},
]


@pytest.fixture()
def lib(tmp_library):
    return tmp_library(REFS, DOCS, LINKS)


def _feather(lib, name):
    return pd.read_feather(lib.config_path / name)


def test_propose_merge_fills_reports_conflicts_and_normalizes():
    keep = {"tag": "K", "type": "article", "title": "{One Work}", "author": "Doe, Jane",
            "journal": "", "added": "x", "arc-source": "y"}
    absorb = {"tag": "A", "type": "article", "title": "One  Work", "author": "Doe, J.",
              "journal": "J. Works", "pages": "1--2"}

    merged, conflicts = propose_merge(keep, absorb)

    assert merged["tag"] == "K"
    assert merged["journal"] == "J. Works" and merged["pages"] == "1--2"  # filled
    assert merged["author"] == "Doe, Jane"  # keep wins
    # braces and whitespace are not a conflict; the author is
    assert [c["field"] for c in conflicts] == ["author"]
    assert "added" not in merged and "arc-source" not in merged


def test_remove_reference_writes_an_audit_copy(lib):
    audit = lib.remove_reference("Dupe2020")

    assert "Dupe2020" not in _feather(lib, "ref.feather").tag.values
    assert "Dupe2020" not in _feather(lib, "ref-doc.feather").tag.values
    assert len(_feather(lib, "doc.feather")) == 3  # documents stay
    text = audit.read_text(encoding="utf-8")
    assert audit.parent.name == "edit-audit" and "_delete_Dupe2020" in audit.name
    assert "@article{Dupe2020," in text
    assert "% added 2022-02-02" in text
    assert f"% link {H2} 0 0" in text and f"% link {H1} 0 1" in text
    with pytest.raises(ValueError, match="not found"):
        lib.remove_reference("Nope")


def test_merge_repoints_links_keeps_earlier_date_and_saves_once(lib, monkeypatch):
    saves = []
    real_save = lib.save
    monkeypatch.setattr(lib, "save", lambda: (saves.append(1), real_save())[1])
    keep_row = lib.ref_df[lib.ref_df.tag == "Keep2020"].iloc[0]
    absorb_row = lib.ref_df[lib.ref_df.tag == "Dupe2020"].iloc[0]
    merged, _ = propose_merge(keep_row, absorb_row)

    audit = lib.merge_references("Keep2020", "Dupe2020", merged)

    assert saves == [1]
    refs = _feather(lib, "ref.feather").set_index("tag")
    assert "Dupe2020" not in refs.index
    assert refs.loc["Keep2020", "journal"] == "J. Works"
    assert refs.loc["Keep2020", "added"] == pd.Timestamp("2022-02-02", tz=TZ)
    links = _feather(lib, "ref-doc.feather")
    keep_links = links[links.tag == "Keep2020"].sort_values("priority")
    # Keep's primary unchanged, Dupe's H2 appended, the duplicate H1 dropped
    assert keep_links[["hash", "priority"]].values.tolist() == [[H1, 0], [H2, 1]]
    assert links[links.tag == "Bystander2019"].hash.tolist() == [H3]
    text = audit.read_text(encoding="utf-8")
    assert "_merge_Dupe2020_into_Keep2020" in audit.name
    assert "@article{Keep2020," in text and "@article{Dupe2020," in text


@pytest.mark.parametrize("keep, absorb, fields, match", [
    ("Keep2020", "Keep2020", {"tag": "Keep2020"}, "into itself"),
    ("Keep2020", "Nope", {"tag": "Keep2020"}, "not found"),
    ("Keep2020", "Dupe2020", {"tag": "Renamed2020"}, "must keep the tag"),
])
def test_merge_rejects(lib, keep, absorb, fields, match):
    with pytest.raises(ValueError, match=match):
        lib.merge_references(keep, absorb, fields)
    assert not (lib.config_path / "edit-audit").exists()


@pytest.fixture()
def client(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_delete_routes(client, lib):
    confirm = client.get("/edit-tag/Dupe2020/delete").get_data(as_text=True)
    assert "Delete Dupe2020 permanently" in confirm
    assert "2 document links" in confirm and "stops resolving" in confirm
    assert "Dupe2020" in _feather(lib, "ref.feather").tag.values  # GET changes nothing

    done = client.post("/edit-tag/Dupe2020/delete").get_data(as_text=True)
    assert "Dupe2020 deleted" in done and "edit-audit" in done
    assert "Dupe2020" not in _feather(lib, "ref.feather").tag.values


def test_merge_routes(client, lib):
    page = client.get("/edit-tag/Keep2020").get_data(as_text=True)
    assert "Merge another reference into Keep2020" in page

    preview = client.get("/edit-tag/Keep2020/merge/preview", query_string={"q": "Dupe2020"}).get_data(as_text=True)
    assert "Merge Dupe2020 into Keep2020" in preview
    assert "already on Keep2020, dropped" in preview and "moves to Keep2020" in preview
    assert "Swap roles" in preview
    merged_bibtex = preview.split('name="merged_bibtex"')[1].split(">", 1)[1].split("</textarea>")[0]

    # a renamed tag is refused and the preview comes back with the reason
    refused = client.post("/edit-tag/Keep2020/merge", data={
        "absorb": "Dupe2020", "merged_bibtex": merged_bibtex.replace("Keep2020", "Other2020", 1)})
    assert "must keep the tag" in refused.get_data(as_text=True)
    assert "HX-Retarget" not in refused.headers

    done = client.post("/edit-tag/Keep2020/merge", data={"absorb": "Dupe2020", "merged_bibtex": merged_bibtex})
    assert done.headers["HX-Retarget"] == "#editor-container"
    assert "Merged Dupe2020 into Keep2020" in done.get_data(as_text=True)
    assert "Dupe2020" not in _feather(lib, "ref.feather").tag.values
