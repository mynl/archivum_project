import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


_UNIVERSE_CACHE = {
    "signature": None,
    "data": {},
}


@dataclass(frozen=True)
class UniverseResult:
    hashes: set[str]
    rg_command: str = ""
    rg_cache_hit: bool = False


def _library_signature(lib) -> tuple:
    paths = [
        lib.config_path / "ref.feather",
        lib.config_path / "doc.feather",
        lib.config_path / "ref-doc.feather",
    ]
    mtimes = tuple(p.stat().st_mtime if p.exists() else 0 for p in paths)
    return (lib.name, *mtimes)


def _get_cached_universe(lib, key):
    signature = _library_signature(lib)
    if _UNIVERSE_CACHE["signature"] != signature:
        _UNIVERSE_CACHE["signature"] = signature
        _UNIVERSE_CACHE["data"] = {}
    cached = _UNIVERSE_CACHE["data"].get(key)
    return set(cached) if cached is not None else None


def _set_cached_universe(lib, key, hashes):
    signature = _library_signature(lib)
    if _UNIVERSE_CACHE["signature"] != signature:
        _UNIVERSE_CACHE["signature"] = signature
        _UNIVERSE_CACHE["data"] = {}
    if len(_UNIVERSE_CACHE["data"]) > 100:
        _UNIVERSE_CACHE["data"].pop(next(iter(_UNIVERSE_CACHE["data"])))
    _UNIVERSE_CACHE["data"][key] = frozenset(hashes)


def split_universe_query(raw_query: str) -> tuple[str, str]:
    """Split the unified network query into querex and ripgrep portions."""
    querex_part = ""
    ripgrep_part = ""
    raw = raw_query.strip()

    if " rg " in raw.lower():
        parts = re.split(r"\s+rg\s+", raw, flags=re.IGNORECASE, maxsplit=1)
        querex_part = parts[0].strip()
        ripgrep_part = parts[1].strip()
        if querex_part.lower().startswith("q "):
            querex_part = querex_part[2:].strip()
    elif raw.lower().startswith("q "):
        querex_part = raw[2:].strip()
    elif raw.lower().startswith("rg "):
        ripgrep_part = raw[3:].strip()
    else:
        querex_part = raw

    return querex_part, ripgrep_part


def _query_hashes(lib, df: pd.DataFrame, querex_part: str) -> set[str]:
    if not querex_part:
        return set(df["hash"].dropna().astype(str))

    q_expr = querex_part
    if "select" not in q_expr.lower():
        match = re.match(r"^((?:top\s+\d+\s+)?(?:recent\s+)?)(.*)$", q_expr, re.IGNORECASE)
        if match:
            prefix, rest = match.groups()
            q_expr = f"{prefix}select hash, tag, author, title, year, * {rest}"
        else:
            q_expr = "select hash, tag, author, title, year, * " + q_expr

    q_result = df.querex(q_expr)
    if not isinstance(q_result, pd.DataFrame):
        raise ValueError(f"Querex error: {q_result}")
    return set(q_result["hash"].dropna().astype(str))


def _ripgrep_args(ripgrep_part: str, *, case_sensitive: bool = False) -> tuple[str, list[str]]:
    is_regex = any(c in ripgrep_part for c in r".*+?^$|()[]{}")
    args = ["-n", "-H", "--pcre2"] if is_regex else ["-n", "-H"]
    if not case_sensitive:
        args.append("-i")

    clean_rg = ripgrep_part
    if " -g " in ripgrep_part:
        rg_bits = ripgrep_part.split(" -g ")
        clean_rg = rg_bits[0].strip()
        for g in rg_bits[1:]:
            args.extend(["-g", g.strip()])
    else:
        args.extend(["-g", "*.md"])

    return clean_rg, args


def _ripgrep_command(clean_rg: str, args: list[str]) -> str:
    return f'rg --line-buffered --stats -C 1 --encoding utf-8 {" ".join(args)} "{clean_rg}" .'


def _ripgrep_hash_prefixes(lib, ripgrep_part: str, *, case_sensitive: bool = False) -> tuple[set[str], bool]:
    clean_rg, args = _ripgrep_args(ripgrep_part, case_sensitive=case_sensitive)
    key = ("rg-prefixes", clean_rg, tuple(args))
    cached = _get_cached_universe(lib, key)
    if cached is not None:
        return cached, True

    prefixes = set()
    _rc, proc = lib.run_ripgrep(clean_rg, args)
    for line in proc.stdout:
        line = line.strip()
        if not line or ":" not in line:
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        prefixes.add(Path(parts[0]).name[:10].upper())

    _set_cached_universe(lib, key, prefixes)
    return prefixes, False


def resolve_universe_details(lib, raw_query: str, *, case_sensitive: bool = False) -> UniverseResult:
    """Resolve a combined querex/ripgrep query to full document hashes."""
    querex_part, ripgrep_part = split_universe_query(raw_query)
    rg_command = ""
    if ripgrep_part:
        clean_rg, args = _ripgrep_args(ripgrep_part, case_sensitive=case_sensitive)
        rg_command = _ripgrep_command(clean_rg, args)

    key = ("universe", raw_query.strip(), case_sensitive)
    cached = _get_cached_universe(lib, key)
    if cached is not None:
        return UniverseResult(hashes=cached, rg_command=rg_command, rg_cache_hit=True)

    df = lib.database

    querex_hashes = _query_hashes(lib, df, querex_part)

    if ripgrep_part:
        rg_prefixes, rg_cache_hit = _ripgrep_hash_prefixes(lib, ripgrep_part, case_sensitive=case_sensitive)
        prefix_to_hashes = {}
        for h in querex_hashes:
            prefix_to_hashes.setdefault(h[:10].upper(), set()).add(h)

        rg_hashes = set()
        for prefix in rg_prefixes:
            rg_hashes.update(prefix_to_hashes.get(prefix, set()))
        _set_cached_universe(lib, key, rg_hashes)
        return UniverseResult(hashes=rg_hashes, rg_command=rg_command, rg_cache_hit=rg_cache_hit)

    _set_cached_universe(lib, key, querex_hashes)
    return UniverseResult(hashes=querex_hashes)


def resolve_universe(lib, raw_query: str, *, case_sensitive: bool = False) -> set[str]:
    """Resolve a combined querex/ripgrep query to full document hashes."""
    return resolve_universe_details(lib, raw_query, case_sensitive=case_sensitive).hashes
