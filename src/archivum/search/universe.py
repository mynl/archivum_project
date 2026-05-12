import re
from pathlib import Path

import pandas as pd


def resolve_universe(lib, raw_query: str) -> set[str]:
    """Resolve a combined querex/ripgrep query to full document hashes."""
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

    df = lib.database

    querex_hashes = set()
    if querex_part:
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
        querex_hashes = set(q_result["hash"].dropna().astype(str))
    else:
        querex_hashes = set(df["hash"].dropna().astype(str))

    if ripgrep_part:
        rg_hashes = set()
        is_regex = any(c in ripgrep_part for c in r".*+?^$|()[]{}")
        args = ["-n", "-H", "--pcre2"] if is_regex else ["-n", "-H"]

        clean_rg = ripgrep_part
        if " -g " in ripgrep_part:
            rg_bits = ripgrep_part.split(" -g ")
            clean_rg = rg_bits[0].strip()
            for g in rg_bits[1:]:
                args.extend(["-g", g.strip()])
        else:
            args.extend(["-g", "*.md"])

        _rc, proc = lib.run_ripgrep(clean_rg, args)
        for line in proc.stdout:
            line = line.strip()
            if not line or ":" not in line:
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            h_prefix = Path(parts[0]).name[:10].upper()
            for h in querex_hashes:
                if h.upper().startswith(h_prefix):
                    rg_hashes.add(h)
        return rg_hashes

    return querex_hashes
