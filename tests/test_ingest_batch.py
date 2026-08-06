"""Batch staging, ref-doc priority, and document replacement."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from archivum.utilities import assign_ref_doc_priority
from archivum.web.services import ingest_batch as ib


# ------------------------------------------------------------------ priority

def _links(*rows):
    return pd.DataFrame(list(rows))


def test_priority_backfill_ranks_by_row_order():
    """A library written before the column existed keeps its .iloc[0] winner."""
    df = _links(
        {"tag": "A", "hash": "h1", "version": 0},
        {"tag": "B", "hash": "h2", "version": 0},
        {"tag": "A", "hash": "h3", "version": 0},
    )
    out = assign_ref_doc_priority(df)
    assert out.priority.tolist() == [0, 0, 1]
    # row order is preserved so the feather stays diff-stable
    assert out.hash.tolist() == ["h1", "h2", "h3"]


def test_priority_backfill_is_idempotent():
    df = _links(
        {"tag": "A", "hash": "h1", "version": 0},
        {"tag": "A", "hash": "h2", "version": 1},
        {"tag": "A", "hash": "h3", "version": 0},
    )
    once = assign_ref_doc_priority(df)
    twice = assign_ref_doc_priority(once)
    assert once.priority.tolist() == twice.priority.tolist() == [0, 1, 2]


def test_priority_respects_existing_values_and_closes_gaps():
    df = _links(
        {"tag": "A", "hash": "h1", "version": 0, "priority": 5},
        {"tag": "A", "hash": "h2", "version": 0, "priority": 2},
    )
    out = assign_ref_doc_priority(df)
    assert out.set_index("hash").priority.to_dict() == {"h2": 0, "h1": 1}


def test_priority_puts_unranked_rows_last():
    df = _links(
        {"tag": "A", "hash": "h1", "version": 0, "priority": None},
        {"tag": "A", "hash": "h2", "version": 0, "priority": 0},
    )
    out = assign_ref_doc_priority(df)
    assert out.set_index("hash").priority.to_dict() == {"h2": 0, "h1": 1}


def test_priority_on_empty_frame_adds_the_column():
    out = assign_ref_doc_priority(pd.DataFrame(columns=["tag", "hash", "version"]))
    assert "priority" in out.columns
    assert out.empty


def test_priority_is_not_version():
    """The same hash at two versions is two documents, both rankable."""
    df = _links(
        {"tag": "A", "hash": "h1", "version": 0},
        {"tag": "A", "hash": "h1", "version": 1},
    )
    out = assign_ref_doc_priority(df)
    assert out.version.tolist() == [0, 1]
    assert out.priority.tolist() == [0, 1]


# ------------------------------------------------------------------ batch staging

@pytest.fixture()
def staging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _upload(name: str):
    from werkzeug.datastructures import FileStorage

    return FileStorage(stream=BytesIO(b"%PDF-1.4\n"), filename=name)


def test_create_batch_gives_each_document_its_own_directory(staging):
    state = ib.create_batch(uploads=[_upload("a.pdf"), _upload("b.pdf")])

    assert len(state["items"]) == 2
    for i in (0, 1):
        p = ib.item_path(state, i)
        assert p.exists()
        # one file per directory: the importer scans doc_dir, so anything else
        # there would be hashed on every preview and every commit
        assert list(p.parent.iterdir()) == [p]


def test_create_batch_globs_a_folder_without_recursing(staging):
    src = staging / "src"
    (src / "nested").mkdir(parents=True)
    (src / "one.pdf").write_bytes(b"%PDF-1.4\n")
    (src / "two.epub").write_bytes(b"x")
    (src / "notes.txt").write_text("skip me")
    (src / "nested" / "deep.pdf").write_bytes(b"%PDF-1.4\n")

    state = ib.create_batch(url_path=str(src))

    names = sorted(it["filename"] for it in state["items"])
    assert names == ["one.pdf", "two.epub"]  # no notes.txt, no deep.pdf


def test_create_batch_rejects_a_folder_with_no_documents(staging):
    empty = staging / "empty"
    empty.mkdir()
    (empty / "readme.md").write_text("nothing here")

    with pytest.raises(ib.BatchError, match="No documents"):
        ib.create_batch(url_path=str(empty))
    assert not list(ib.STAGING_ROOT.iterdir())


def test_batch_id_rejects_path_traversal(staging):
    for bad in ["../../etc", "..", "a/b", "", "ZZZZZZZZZZZZ", "abc"]:
        with pytest.raises(ib.BatchError):
            ib.batch_dir(bad)


def test_next_pending_walks_the_queue_and_counts_settle(staging):
    state = ib.create_batch(uploads=[_upload(f"{i}.pdf") for i in range(3)])
    bid = state["batch_id"]

    assert ib.next_pending(state) == 0
    ib.mark(bid, 0, "done", tag="A2024")
    state = ib.load_batch(bid)
    assert ib.next_pending(state, after=0) == 1

    ib.mark(bid, 1, "skipped")
    ib.mark(bid, 2, "replaced", tag="B2020")
    state = ib.load_batch(bid)
    assert ib.next_pending(state, after=-1) is None
    assert ib.counts(state) == {
        "total": 3, "pending": 0, "done": 1, "skipped": 1,
        "failed": 0, "replaced": 1, "settled": 3,
    }


def test_concurrent_marks_do_not_clobber_each_other(staging):
    """The prefetch thread and the request thread both write the manifest."""
    import threading

    state = ib.create_batch(uploads=[_upload(f"{i}.pdf") for i in range(6)])
    bid = state["batch_id"]
    barrier = threading.Barrier(2)

    def worker(indices, status, key):
        barrier.wait()
        for i in indices:
            ib.mark(bid, i, status, **{key: f"v{i}"})

    threads = [
        threading.Thread(target=worker, args=(range(0, 3), "done", "tag")),
        threading.Thread(target=worker, args=(range(3, 6), "skipped", "message")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = ib.load_batch(bid)
    assert [it["status"] for it in final["items"]] == ["done"] * 3 + ["skipped"] * 3
    assert [it["tag"] for it in final["items"][:3]] == ["v0", "v1", "v2"]
    assert [it["message"] for it in final["items"][3:]] == ["v3", "v4", "v5"]


def test_cache_prepared_never_resurrects_a_settled_document(staging):
    """
    Prefetch works from a snapshot. If the user settles a document while it is
    being analysed, writing the result back must not restore 'pending'.
    """
    state = ib.create_batch(uploads=[_upload("a.pdf"), _upload("b.pdf")])
    bid = state["batch_id"]

    ib.mark(bid, 1, "skipped")
    ib.cache_prepared(bid, 1, {"hash": "H", "bibtex": "@article{x}"})

    it = ib.item(ib.load_batch(bid), 1)
    assert it["status"] == "skipped"
    assert it["prepared"]["hash"] == "H"


def test_prefetch_skips_the_document_the_caller_renders(staging):
    state = ib.create_batch(uploads=[_upload(f"{i}.pdf") for i in range(3)])
    bid = state["batch_id"]
    seen = []
    done = __import__("threading").Event()

    def prepare(path):
        seen.append(path.name)
        if len(seen) == 2:
            done.set()
        return {"hash": path.name, "bibtex": "", "candidates": {}, "error": None}

    ib.start_prefetch(bid, prepare)
    done.wait(timeout=10)

    assert "0.pdf" not in seen, "document 0 is rendered inline; prefetching it duplicates work"
    assert sorted(seen) == ["1.pdf", "2.pdf"]


def test_discard_batch_removes_staging_but_not_the_source(staging):
    src = staging / "src"
    src.mkdir()
    original = src / "book.pdf"
    original.write_bytes(b"%PDF-1.4\n")

    state = ib.create_batch(url_path=str(src))
    bid = state["batch_id"]
    assert ib.batch_dir(bid).exists()

    ib.discard_batch(bid)
    assert not ib.batch_dir(bid).exists()
    assert original.exists(), "cancelling a batch must never touch the originals"


def test_load_batch_after_discard_is_an_error(staging):
    state = ib.create_batch(uploads=[_upload("a.pdf")])
    ib.discard_batch(state["batch_id"])
    with pytest.raises(ib.BatchError, match="not found"):
        ib.load_batch(state["batch_id"])


def test_sweep_stale_removes_only_old_batches(staging):
    import os
    import time

    fresh = ib.create_batch(uploads=[_upload("new.pdf")])
    old = ib.create_batch(uploads=[_upload("old.pdf")])
    old_dir = ib.batch_dir(old["batch_id"])
    stale = time.time() - (ib.STALE_BATCH_SECONDS + 60)
    os.utime(old_dir, (stale, stale))

    assert ib.sweep_stale() == 1
    assert not old_dir.exists()
    assert ib.batch_dir(fresh["batch_id"]).exists()


# ------------------------------------------------------------------ replacement

def test_unlink_and_replace_reorder_priority(tmp_path, monkeypatch):
    """
    replace_document links the new file at priority 0 and demotes the old one,
    keeping the old doc row and its sharded hard link.
    """
    from types import SimpleNamespace

    from archivum.library import Library

    store = tmp_path / "store"
    store.mkdir()
    staged = tmp_path / "staged" / "new.pdf"
    staged.parent.mkdir()
    staged.write_bytes(b"%PDF-1.4\nnew content\n")

    lib = Library.__new__(Library)
    lib.config_path = tmp_path / "lib"
    lib.config_path.mkdir()
    lib.doc_store_path = store
    lib.text_dir_path = tmp_path / "text"
    lib.text_dir_path.mkdir()
    lib.config = SimpleNamespace(
        hash_workers=1, timezone="UTC", extractor="noop", ref_columns=["tag", "title", "author", "year"]
    )
    lib._ref_df = pd.DataFrame([
        {"tag": "Old2020", "title": "A Paper", "author": "Smith, J", "year": "2020"}
    ])
    lib._doc_df = pd.DataFrame([{
        "name": "old.pdf", "path": "ol/old.pdf", "mod": pd.NaT, "create": pd.NaT,
        "access": pd.NaT, "node": 0, "links": 1, "size": 1, "suffix": ".pdf",
        "hash": "OLDHASH", "version": 0,
    }])
    lib._ref_doc_df = assign_ref_doc_priority(
        pd.DataFrame([{"tag": "Old2020", "hash": "OLDHASH", "version": 0}])
    )
    saved = {"count": 0}
    lib.save = lambda: saved.__setitem__("count", saved["count"] + 1)

    result = lib.replace_document("Old2020", staged, extract_text=False)

    links = lib._ref_doc_df.sort_values("priority")
    assert links.priority.tolist() == [0, 1]
    assert links.iloc[0].hash == result["hash"] != "OLDHASH"
    assert links.iloc[1].hash == "OLDHASH"
    assert saved["count"] == 1, "replacement should be a single save"

    # the old doc row survives; the new one is sharded under the ref's metadata
    assert "OLDHASH" in lib._doc_df.hash.tolist()
    new_row = lib._doc_df[lib._doc_df.hash == result["hash"]].iloc[0]
    assert "2020_Smith" in new_row["name"]
    assert Path(new_row["path"]).exists()

    # primary_doc resolves to the replacement
    assert lib.primary_doc("Old2020")["hash"] == result["hash"]

    # unlink drops the demoted row and renumbers
    assert lib.unlink_document("Old2020", "OLDHASH", save=False) == 1
    assert lib._ref_doc_df.priority.tolist() == [0]


def test_promote_document_undoes_a_replacement(tmp_path):
    """Replacement is reversible: promoting the demoted hash restores it."""
    from types import SimpleNamespace

    from archivum.library import Library

    lib = Library.__new__(Library)
    lib.config = SimpleNamespace()
    lib._ref_doc_df = assign_ref_doc_priority(pd.DataFrame([
        {"tag": "A", "hash": "NEW", "version": 0, "priority": 0},
        {"tag": "A", "hash": "OLD", "version": 0, "priority": 1},
        {"tag": "B", "hash": "OTHER", "version": 0, "priority": 0},
    ]))

    lib.promote_document("A", "OLD", save=False)

    got = lib._ref_doc_df.set_index("hash").priority.to_dict()
    assert got == {"OLD": 0, "NEW": 1, "OTHER": 0}, "other tags must be untouched"

    # and back again
    lib.promote_document("A", "NEW", save=False)
    assert lib._ref_doc_df.set_index("hash").priority.to_dict()["NEW"] == 0


def test_promote_document_rejects_an_unlinked_hash(tmp_path):
    from types import SimpleNamespace

    from archivum.library import Library

    lib = Library.__new__(Library)
    lib.config = SimpleNamespace()
    lib._ref_doc_df = assign_ref_doc_priority(
        pd.DataFrame([{"tag": "A", "hash": "NEW", "version": 0}])
    )
    with pytest.raises(ValueError, match="No link"):
        lib.promote_document("A", "NOPE", save=False)


def test_mutations_tolerate_a_missing_priority_column(tmp_path):
    """A frame set directly, without the column, must not KeyError."""
    from types import SimpleNamespace

    from archivum.library import Library

    lib = Library.__new__(Library)
    lib.config = SimpleNamespace()
    # deliberately no priority column
    lib._ref_doc_df = pd.DataFrame([
        {"tag": "A", "hash": "H1", "version": 0},
        {"tag": "A", "hash": "H2", "version": 0},
    ])

    lib.promote_document("A", "H2", save=False)
    assert lib._ref_doc_df.set_index("hash").priority.to_dict() == {"H2": 0, "H1": 1}
    assert lib.unlink_document("A", "H1", save=False) == 1
    assert lib._ref_doc_df.priority.tolist() == [0]


def test_replace_document_rejects_the_same_file(tmp_path):
    from types import SimpleNamespace

    from archivum.library import Library, LibraryImportBlocked

    staged = tmp_path / "same.pdf"
    staged.write_bytes(b"%PDF-1.4\n")

    lib = Library.__new__(Library)
    lib.config_path = tmp_path
    lib.doc_store_path = tmp_path / "store"
    lib.config = SimpleNamespace(hash_workers=1, timezone="UTC")
    lib._ref_df = pd.DataFrame([{"tag": "Old2020", "title": "T", "author": "A", "year": "2020"}])
    lib._doc_df = pd.DataFrame(columns=["hash", "version", "path"])
    row = lib.register_document(staged)
    lib._ref_doc_df = assign_ref_doc_priority(
        pd.DataFrame([{"tag": "Old2020", "hash": row["hash"], "version": 0}])
    )

    with pytest.raises(LibraryImportBlocked, match="already linked"):
        lib.replace_document("Old2020", staged, extract_text=False)


def test_import_conflict_marks_merge_warn_resolvable():
    from archivum.library import Library

    analysis = pd.DataFrame([{
        "tag": "New2024", "title": "A Paper",
        "action": "Merge/Warn", "match tag": "Old2020",
    }])
    conflict = Library.import_conflict(analysis)
    assert conflict["resolvable"] is True
    assert conflict["match_tag"] == "Old2020"


def test_import_conflict_marks_hash_dupe_unresolvable():
    from archivum.library import Library

    analysis = pd.DataFrame([{
        "tag": "New2024", "title": "A Paper",
        "action": "SKIP (Dupe)", "match tag": "Old2020",
    }])
    conflict = Library.import_conflict(analysis)
    assert conflict["resolvable"] is False
    assert "SKIP (Dupe)" in conflict["message"]


def test_import_conflict_none_when_clean():
    from archivum.library import Library

    analysis = pd.DataFrame([{"tag": "New2024", "action": "Import", "match tag": ""}])
    assert Library.import_conflict(analysis) is None
