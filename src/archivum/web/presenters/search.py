import pandas as pd

from ...utilities import clean_latex, trim_author


def prepare_search_results(df):
    """Clean and format a DataFrame for display in search results."""
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

    return display_results
