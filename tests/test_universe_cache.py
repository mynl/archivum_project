from __future__ import annotations

from types import SimpleNamespace

import pandas as pd


def _write_library_files(tmp_path):
    for name in ["ref.feather", "doc.feather", "ref-doc.feather"]:
        (tmp_path / name).write_text("x", encoding="utf-8")


def _database(rows, querex_rows=None):
    df = pd.DataFrame(rows)

    def querex(_expr):
        return pd.DataFrame(querex_rows if querex_rows is not None else rows)

    object.__setattr__(df, "querex", querex)
    return df


class FakeLib:
    def __init__(self, tmp_path, database, rg_lines=None):
        self.name = "fake-lib"
        self.config_path = tmp_path
        self.database = database
        self.rg_lines = rg_lines or []
        self.rg_calls = 0
        self.rg_patterns = []
        self.rg_args = []

    def run_ripgrep(self, pattern, args):
        self.rg_calls += 1
        self.rg_patterns.append(pattern)
        self.rg_args.append(list(args))
        return 0, SimpleNamespace(stdout=list(self.rg_lines))


def test_resolve_universe_caches_pure_q_query(tmp_path):
    from archivum.search import universe

    universe._UNIVERSE_CACHE["signature"] = None
    universe._UNIVERSE_CACHE["data"] = {}
    _write_library_files(tmp_path)
    calls = {"querex": 0}
    df = pd.DataFrame([{"hash": "ABCDEF123456"}])

    def querex(_expr):
        calls["querex"] += 1
        return pd.DataFrame([{"hash": "ABCDEF123456"}])

    object.__setattr__(df, "querex", querex)
    lib = FakeLib(tmp_path, df)

    assert universe.resolve_universe(lib, "q title ~ /risk/") == {"ABCDEF123456"}
    assert universe.resolve_universe(lib, "q title ~ /risk/") == {"ABCDEF123456"}
    assert calls["querex"] == 1


def test_resolve_universe_caches_pure_rg_query(tmp_path):
    from archivum.search import universe

    universe._UNIVERSE_CACHE["signature"] = None
    universe._UNIVERSE_CACHE["data"] = {}
    _write_library_files(tmp_path)
    df = _database(
        [
            {"hash": "ABCDEF123456"},
            {"hash": "999999999999"},
        ]
    )
    lib = FakeLib(tmp_path, df, rg_lines=["ABCDEF1234_doc.md:1:match\n"])

    assert universe.resolve_universe(lib, "rg risk") == {"ABCDEF123456"}
    assert universe.resolve_universe(lib, "rg risk") == {"ABCDEF123456"}
    assert lib.rg_calls == 1
    assert "-i" in lib.rg_args[0]


def test_resolve_universe_combined_query_intersects_by_prefix(tmp_path):
    from archivum.search import universe

    universe._UNIVERSE_CACHE["signature"] = None
    universe._UNIVERSE_CACHE["data"] = {}
    _write_library_files(tmp_path)
    df = _database(
        [{"hash": "SHOULDNOTUSE"}],
        querex_rows=[
            {"hash": "ABCDEF123456"},
            {"hash": "999999999999"},
        ],
    )
    lib = FakeLib(
        tmp_path,
        df,
        rg_lines=[
            "ABCDEF1234_doc.md:1:match\n",
            "NOQUERY0000_doc.md:2:match\n",
        ],
    )

    assert universe.resolve_universe(lib, "q author ~ /Smith/ rg risk") == {"ABCDEF123456"}


def test_resolve_universe_rg_case_sensitive_omits_i(tmp_path):
    from archivum.search import universe

    universe._UNIVERSE_CACHE["signature"] = None
    universe._UNIVERSE_CACHE["data"] = {}
    _write_library_files(tmp_path)
    df = _database([{"hash": "ABCDEF123456"}])
    lib = FakeLib(tmp_path, df, rg_lines=["ABCDEF1234_doc.md:1:match\n"])

    assert universe.resolve_universe(lib, "rg Risk", case_sensitive=True) == {"ABCDEF123456"}
    assert "-i" not in lib.rg_args[0]


def test_resolve_universe_details_reports_rg_command(tmp_path):
    from archivum.search import universe

    universe._UNIVERSE_CACHE["signature"] = None
    universe._UNIVERSE_CACHE["data"] = {}
    _write_library_files(tmp_path)
    df = _database([{"hash": "ABCDEF123456"}])
    lib = FakeLib(tmp_path, df, rg_lines=["ABCDEF1234_doc.md:1:match\n"])

    result = universe.resolve_universe_details(lib, "rg climate risk")

    assert result.hashes == {"ABCDEF123456"}
    assert result.rg_command == 'rg --line-buffered --stats -C 1 --encoding utf-8 -n -H -i -g *.md "climate risk" .'
    assert result.rg_cache_hit is False
