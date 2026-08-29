"""
ReFS volumes (Windows Dev Drives) report 128-bit file IDs, which overflow the
int64 ``node`` column and crashed the Feather write mid-save. The inode is
recorded as 0 when it does not fit.
"""
import os
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa

from archivum.utilities import inode_or_zero


def test_inode_fits_passes_through():
    assert inode_or_zero(SimpleNamespace(st_ino=1125899906851821)) == 1125899906851821


def test_inode_overflow_becomes_zero():
    refs_inode = 240619329697467391279119  # a real V: Dev Drive value, 78 bits
    assert inode_or_zero(SimpleNamespace(st_ino=refs_inode)) == 0
    # and the value it replaces really would not fit
    try:
        pa.array([refs_inode], type=pa.int64())
    except (OverflowError, pa.ArrowInvalid):
        pass
    else:
        raise AssertionError("expected the raw inode to overflow int64")


def test_real_stat_survives_int64(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("x")
    node = inode_or_zero(os.stat(p))
    pd.DataFrame({"node": [node]}).astype({"node": "int64"})
