from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from flask import send_file
import pandas as pd

from ...bibtex import rows_to_bibtex


def export_dataframe_to_csv(export_df: pd.DataFrame, filename: str):
    """Write a dataframe export to temp and return it as a download."""
    temp_path = _temp_export_path(filename)
    export_df.to_csv(temp_path, index=False, encoding="utf-8-sig")
    return send_file(str(temp_path.absolute()), as_attachment=True, download_name=filename)


def export_dataframe_to_bibtex(export_df: pd.DataFrame, lib, *, plus: bool = False):
    """Write BibTeX for a dataframe export using the library BibTeX field list."""
    bibtex = rows_to_bibtex(
        _prefer_database_rows(export_df, lib),
        allowed_fields=list(lib.config.ref_columns),
        include_hash=plus,
        include_file=plus,
        path_resolver=lib.abspath,
    )
    if not bibtex:
        return "No BibTeX entries found to export.", 400

    filename = "arc-bibtex-plus-export.bib" if plus else "arc-bibtex-export.bib"
    temp_path = _temp_export_path(filename)
    temp_path.write_text(bibtex + "\n", encoding="utf-8")
    return send_file(str(temp_path.absolute()), as_attachment=True, download_name=filename)


def query_export_filename(raw_query: str) -> str:
    """Return the existing dated CSV filename format for query exports."""
    return f"arc-{_date_str()}-{_clean_query(raw_query)}.csv"


def galaxy_export_filename(raw_query: str) -> str:
    """Return the existing dated CSV filename format for semantic exports."""
    return f"arc-galaxy-{_date_str()}-{_clean_query(raw_query)}.csv"


def _prefer_database_rows(export_df: pd.DataFrame, lib) -> pd.DataFrame:
    """
    Rebuild rows from ``lib.database`` when an export contains selected columns.

    Query, Ripgrep, and Network result dataframes can contain selected columns or
    derived network columns. Matching back by tag/hash preserves configured
    BibTeX fields while retaining document path/hash for BibTeX+.
    """
    if not isinstance(export_df, pd.DataFrame) or export_df.empty:
        return pd.DataFrame()

    database = lib.database
    if not isinstance(database, pd.DataFrame) or database.empty:
        return export_df

    if "hash" in export_df.columns and "hash" in database.columns:
        hashes = _clean_string_values(export_df["hash"])
        if hashes:
            hash_mask = [
                bool(value) and any(value.startswith(h) or h.startswith(value) for h in hashes)
                for value in _clean_string_values(database["hash"], keep_empty=True)
            ]
            matched = database[hash_mask]
            if not matched.empty:
                return matched

    if "tag" in export_df.columns and "tag" in database.columns:
        tags = _clean_string_values(export_df["tag"])
        if tags:
            matched = database[database["tag"].astype(str).isin(tags)]
            if not matched.empty:
                return matched

    return export_df


def _temp_export_path(filename: str) -> Path:
    temp_dir = Path("temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir / filename


def _date_str() -> str:
    return datetime.now().strftime("%m-%d")


def _clean_query(raw_query: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw_query).strip("-")[:30]


def _clean_string_values(series: pd.Series, *, keep_empty: bool = False) -> list[str]:
    values = []
    for value in series.tolist():
        if pd.isna(value):
            text = ""
        else:
            text = str(value).strip()
        if text or keep_empty:
            values.append(text)
    return list(dict.fromkeys(values)) if not keep_empty else values
