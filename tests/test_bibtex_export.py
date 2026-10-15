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


def test_bibtex_plus_paths_point_at_the_real_store(tmp_path, monkeypatch):
    from archivum.library import Library

    real_root = tmp_path / "ShardedDocLibrary"
    lib = Library.__new__(Library)
    # the app-home link that lib.abspath joins onto; it must not appear
    lib.doc_store_path = tmp_path / "app-home" / "docs"
    monkeypatch.setattr(lib, "doc_store_real_path", real_root, raising=False)
    absolute = tmp_path / "elsewhere" / "kept.pdf"
    df = pd.DataFrame(
        [
            {"tag": "Rel2020", "type": "article", "title": "Relative", "hash": "AAA",
             "path": "ab/rel.pdf"},
            {"tag": "Abs2021", "type": "article", "title": "Absolute", "hash": "BBB",
             "path": str(absolute)},
        ]
    )

    bibtex = rows_to_bibtex(
        df,
        allowed_fields=["tag", "type", "title"],
        include_hash=True,
        include_file=True,
        path_resolver=lib.store_path,
    )

    assert format_mendeley_file(real_root / "ab" / "rel.pdf") in bibtex
    assert format_mendeley_file(absolute) in bibtex
    assert "app-home" not in bibtex
