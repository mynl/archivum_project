from dataclasses import dataclass


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
    if lower_query.startswith("f "):
        return "f", raw[2:].strip()
    return "f", raw


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
    """Convert a web search string into a querex expression."""
    kind, query = split_query(raw_query)
    if fuzzy_projection is None:
        fuzzy_projection = projection
    if q_projection is None:
        q_projection = projection

    if kind == "f":
        if not query and default_empty_pattern is not None:
            query = default_empty_pattern

        if query and query[0] != "!" and query.find("~") == -1:
            prefix = ""
            if recent:
                prefix += "recent "
            if default_limit is not None:
                prefix += f"top {default_limit} "
            expression = f"{prefix}select {fuzzy_projection} tag ~ {query}"
        else:
            expression = query
            if "select" not in expression.lower():
                expression = f"select {projection} " + expression
            if default_limit is not None and "top" not in expression.lower():
                expression = f"top {default_limit} " + expression
            if recent and "recent" not in expression.lower():
                expression = "recent " + expression
    else:
        expression = query
        if "select" not in expression.lower():
            expression = f"select {q_projection} " + expression

    return QuerySpec(raw=raw_query, kind=kind, query=query, expression=expression)
