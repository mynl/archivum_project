"""The Read blend: read_rank = log2(count) + days / half-life ([query-buttons])."""

from __future__ import annotations

import math

import pandas as pd
import pytest

NOW = pd.Timestamp.now().floor("s")
# tag: (read_count, days since last read); None = never read
READS = {
    "Eight90": (8, 90),
    "OnceToday": (1, 0),
    "FourLastWeek": (4, 7),
    "OnceYesterday": (1, 1),
    "Unread": None,
}


def _library(tmp_library, **config):
    tags = list(READS)
    refs = [{"tag": t, "type": "article", "title": t} for t in tags]
    docs = [{"hash": f"h{i}", "version": 0, "path": f"x/{i}.pdf",
             "create": pd.Timestamp("2024-01-01", tz="Europe/London")} for i in range(len(tags))]
    links = [{"tag": t, "hash": f"h{i}", "version": 0} for i, t in enumerate(tags)]
    reads = [{"hash": f"h{i}", "last_read": NOW - pd.Timedelta(days=READS[t][1]),
              "read_count": READS[t][0], "last_caller": "test"}
             for i, t in enumerate(tags) if READS[t] is not None]
    return tmp_library(refs, docs, links, reads, **config)


def test_read_rank_orders_as_the_half_life_says(tmp_library):
    rank = _library(tmp_library).database.set_index("tag")["read_rank"]

    # one doubling of reads is worth 30 days: 8 reads 90 days ago ties one read today
    assert rank["Eight90"] == pytest.approx(rank["OnceToday"])
    assert rank["FourLastWeek"] > rank["OnceYesterday"]
    assert math.isnan(rank["Unread"])


def test_read_rank_responds_to_the_half_life(tmp_library):
    rank = _library(tmp_library, read_half_life_days=1.0).database.set_index("tag")["read_rank"]

    # with a one-day half-life recency dominates: yesterday's single read wins
    assert rank["OnceYesterday"] > rank["FourLastWeek"]
