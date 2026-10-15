import pandas as pd

from ...utilities import clean_latex, trim_author


def _date_display(values: pd.Series) -> pd.Series:
    """``YYYY-MM-DD`` in each value's own timezone; blank for missing.

    Per value rather than vectorized: converting a column to UTC first would
    move late-evening local times onto the next day.
    """
    def fmt(value):
        if value is None or pd.isna(value):
            return ""
        try:
            return pd.Timestamp(value).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return ""

    return values.map(fmt)


def _activity_display(display_results: pd.DataFrame, tag_col, activity) -> None:
    """
    Add ``added_display``, ``reads_display`` and ``has_doc`` from ``activity``.

    ``activity`` is library-wide per-tag data (``tag`` plus any of ``added``,
    ``read_count``, ``docs``), joined by tag so the fields appear whatever the
    query selected. Without it the result frame's own columns are used.
    Zero reads show blank, not ``×0``. ``has_doc`` is True, False, or None
    when unknown (no ``docs`` column), so the template can tell "no document"
    from "hash not selected".
    """
    if isinstance(activity, pd.DataFrame) and tag_col and "tag" in activity.columns:
        by_tag = activity.drop_duplicates("tag").set_index("tag")
        tags = display_results[tag_col]

        def lookup(column):
            if column not in by_tag.columns:
                return pd.Series(None, index=display_results.index, dtype=object)
            return tags.map(by_tag[column])
    else:
        def lookup(column):
            if column not in display_results.columns:
                return pd.Series(None, index=display_results.index, dtype=object)
            return display_results[column]

    display_results["added_display"] = _date_display(lookup("added"))
    reads = pd.to_numeric(lookup("read_count"), errors="coerce").fillna(0).astype(int)
    display_results["reads_display"] = reads.map(lambda n: f"×{n}" if n > 0 else "")
    docs = pd.to_numeric(lookup("docs"), errors="coerce")
    display_results["has_doc"] = docs.map(lambda n: None if pd.isna(n) else bool(n > 0))


def prepare_search_results(df, activity=None):
    """
    Clean and format a DataFrame for display in search results.

    Parameters
    ----------
    df : pandas.DataFrame
        The query result; any columns.
    activity : pandas.DataFrame, optional
        Library-wide per-tag ``added``, ``read_count`` and ``docs``, joined by
        tag for the dense line's date added, read count, and "no doc" marker.

    Returns
    -------
    pandas.DataFrame
        A copy with ``*_display`` columns added.
    """
    if not isinstance(df, pd.DataFrame):
        return df

    display_results = df.copy()

    def find_col(df, target):
        cols = {c.lower(): c for c in df.columns}
        return cols.get(target.lower())

    type_col = find_col(display_results, "type")
    year_col = find_col(display_results, "year")
    title_col = find_col(display_results, "title")
    author_col = find_col(display_results, "author")
    journal_col = find_col(display_results, "journal")
    publisher_col = find_col(display_results, "publisher")

    for col in [title_col, author_col, journal_col, publisher_col]:
        if col:
            display_results[col] = display_results[col].apply(clean_latex)

    if author_col:
        display_results["author_display"] = display_results[author_col].apply(trim_author)
    else:
        display_results["author_display"] = ""

    if type_col:
        display_results["is_book"] = (
            display_results[type_col].fillna("").astype(str).str.lower().isin(["book", "@book"])
        )
    else:
        display_results["is_book"] = False

    if year_col:
        display_results["year_display"] = (
            display_results[year_col]
            .fillna("")
            .astype(str)
            .apply(lambda x: x.split(".")[0] if "." in x else x)
        )
    else:
        display_results["year_display"] = ""

    if title_col:
        display_results["title_display"] = display_results[title_col]
    else:
        display_results["title_display"] = "[No Title]"

    hash_col = find_col(display_results, "hash")
    if hash_col:
        display_results["hash_display"] = display_results[hash_col].fillna("").astype(str).str[:8]
    else:
        display_results["hash_display"] = ""

    _activity_display(display_results, find_col(display_results, "tag"), activity)

    return display_results
