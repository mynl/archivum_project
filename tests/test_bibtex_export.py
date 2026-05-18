from pathlib import Path
import re

import pandas as pd

from archivum.bibtex import dict_to_bibtex, format_mendeley_file, rows_to_bibtex


def test_dict_to_bibtex_restricts_unknown_type_to_misc():
    bibtex = dict_to_bibtex(
        {"tag": "Smith2024", "type": "manual", "title": "A Paper"},
        allowed_fields=["tag", "type", "title"],
    )

    assert bibtex.startswith("@misc{Smith2024,")
    assert "title = {{A Paper}}" in bibtex


def test_rows_to_bibtex_plus_adds_hash_and_raw_mendeley_file():
    df = pd.DataFrame(
        [
            {
                "tag": "Dean2005",
                "type": "article",
                "title": "Topics in Credibility Theory",
                "hash": "ABC123",
                "path": Path("C:/Users/steve/MendeleyLibrary/Dean/2005 - Dean - Topics.pdf"),
            }
        ]
    )

    bibtex = rows_to_bibtex(
        df,
        allowed_fields=["tag", "type", "title"],
        include_hash=True,
        include_file=True,
    )

    assert "@article{Dean2005," in bibtex
    assert re.search(r"hash\s+= \{ABC123\}", bibtex)
    assert re.search(
        r"file\s+= \{:C\\:/Users/steve/MendeleyLibrary/Dean/2005 - Dean - Topics\.pdf:pdf\}",
        bibtex,
    )


def test_format_mendeley_file_handles_windows_style_path_string():
    assert (
        format_mendeley_file("C:/Users/steve/MendeleyLibrary/Dean/paper.pdf")
        == ":C\\:/Users/steve/MendeleyLibrary/Dean/paper.pdf:pdf"
    )
