"""Regression guard for read-route write amplification.

``Library.record_read`` used to call the full ``Library.save()`` on every PDF
view, which rewrote all four Feathers and rebuilt ``bibtex.bib`` from scratch.
It now persists only ``read.feather`` via ``Library.save_read()``. These tests
lock that scope in: a read event must bump the counter and touch only
``read.feather``, never ref/doc/ref-doc/bibtex.

The tests snapshot and restore ``read.feather`` so they do not permanently
mutate the active (possibly production) library.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


def _read_count_for(lib, file_hash: str) -> int:
    df = lib.read_df
    match = df[df["hash"] == file_hash]
    if match.empty:
        return 0
    return int(match["read_count"].iloc[0])


@pytest.mark.active_library
def test_record_read_only_touches_read_feather(active_library):
    lib = active_library
    if lib.doc_df.empty:
        pytest.skip("Active library has no documents to read.")

    file_hash = str(lib.doc_df["hash"].iloc[0])
    config_path: Path = lib.config_path

    read_path = config_path / "read.feather"
    other_files = [
        config_path / "ref.feather",
        config_path / "doc.feather",
        config_path / "ref-doc.feather",
        config_path / "bibtex.bib",
    ]

    # Snapshot read.feather so we can restore it afterwards.
    read_backup = read_path.read_bytes() if read_path.exists() else None
    before_mtimes = {
        p: (p.stat().st_mtime_ns if p.exists() else None) for p in other_files
    }
    before_count = _read_count_for(lib, file_hash)

    try:
        lib.record_read(file_hash, caller="pytest")

        # The counter advanced and is durable on disk.
        assert _read_count_for(lib, file_hash) == before_count + 1
        assert read_path.exists()
        reloaded = pd.read_feather(read_path)
        persisted = reloaded[reloaded["hash"] == file_hash]["read_count"]
        assert not persisted.empty
        assert int(persisted.iloc[0]) == before_count + 1

        # Nothing else was rewritten — this is the actual regression guard.
        for p in other_files:
            assert (
                p.stat().st_mtime_ns if p.exists() else None
            ) == before_mtimes[p], f"{p.name} was rewritten by record_read"
    finally:
        # Restore read.feather and force an in-memory reload so the session
        # library is not left mutated for other tests.
        if read_backup is not None:
            read_path.write_bytes(read_backup)
        elif read_path.exists():
            read_path.unlink()
        lib._read_df = pd.DataFrame()
        lib._database = pd.DataFrame()
