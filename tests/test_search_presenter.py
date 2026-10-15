"""The dense result line's date added, read count, and "no doc" marker."""

from __future__ import annotations

import pandas as pd

from archivum.web.presenters.search import prepare_search_results

TZ = "Europe/London"


def _activity():
    return pd.DataFrame(
        {
            "tag": ["Read5", "Unread", "Undated", "RefOnly"],
            "added": pd.to_datetime(
                ["2025-07-01 23:30", "2024-01-02 08:00", None, "2026-01-01 00:00"]
            ).tz_localize(TZ),
            "read_count": [5, 0, 0, 0],
            "docs": [1, 1, 1, 0],
        }
    )


def test_activity_joins_by_tag_whatever_the_query_selected():
    # a query whose select dropped added, read_count, and hash
    result = pd.DataFrame({"tag": ["Read5", "Unread", "Undated", "RefOnly"], "title": list("abcd")})

    out = prepare_search_results(result, activity=_activity()).set_index("tag")

    # 23:30 BST stays on the local date (no UTC shift)
    assert out.loc["Read5", "added_display"] == "2025-07-01"
    assert out.loc["Undated", "added_display"] == ""
    assert out.loc["Read5", "reads_display"] == "×5"
    assert out.loc["Unread", "reads_display"] == ""  # zero reads are not shown
    # numpy bools when every tag is known; None only when docs is unknown
    assert out.loc["RefOnly", "has_doc"] is not None and not out.loc["RefOnly", "has_doc"]
    assert out.loc["Unread", "has_doc"]


def test_without_activity_uses_the_frame_and_tolerates_missing_columns():
    out = prepare_search_results(pd.DataFrame({"tag": ["A"], "title": ["x"]}))
    assert out.loc[0, "added_display"] == ""
    assert out.loc[0, "reads_display"] == ""
    assert out.loc[0, "has_doc"] is None
