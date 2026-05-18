from dataclasses import dataclass


QUERY_HELP_TEXT = "Start typing for fuzzy match or enter querex string for recent top 50 or q querex string."
QUEREX_SYMBOLS = frozenset("#~=<>!")


@dataclass(frozen=True)
class QuerySpec:
    raw: str
    kind: str
    query: str
    expression: str


def split_query(raw_query: str) -> tuple[str, str]:
    raw = raw_query.strip()
    lower_query = raw.lower()
    if lower_query.startswith("q "):
        return "q", raw[2:].strip()
    return "raw", raw


def has_querex_symbol(query: str) -> bool:
    """Return True when query contains explicit querexfuzz syntax markers."""
    return any(symbol in query for symbol in QUEREX_SYMBOLS)


def normalize_query(
    raw_query: str,
    *,
    default_limit: int | None = 50,
    recent: bool = True,
    projection: str = "type, *",
    q_projection: str | None = None,
    fuzzy_projection: str | None = None,
    default_empty_pattern: str | None = None,
) -> QuerySpec:
    """Convert a web search string into the expression passed to querexfuzz."""
    kind, query = split_query(raw_query)

    if not query and default_empty_pattern is not None:
        query = default_empty_pattern

    if not query:
        expression = ""
    elif kind == "q":
        expression = query
    elif has_querex_symbol(query):
        prefix = ""
        if recent:
            prefix += "recent "
        if default_limit is not None:
            prefix += f"top {default_limit} "
        expression = f"{prefix}{query}"
    else:
        expression = f"# {query}"

    return QuerySpec(raw=raw_query, kind=kind, query=query, expression=expression)
