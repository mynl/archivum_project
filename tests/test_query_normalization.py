from archivum.search.query import normalize_query, split_query


def test_plain_text_becomes_hash_fuzzy_query():
    spec = normalize_query("spectral risk measure")

    assert spec.kind == "raw"
    assert spec.query == "spectral risk measure"
    assert spec.expression == "# spectral risk measure"


def test_querex_symbol_input_gets_recent_top_defaults():
    assert normalize_query("!Wang").expression == "recent top 50 !Wang"
    assert normalize_query("author ~ /Wang/").expression == "recent top 50 author ~ /Wang/"
    assert normalize_query("year > 2020").expression == "recent top 50 year > 2020"


def test_q_prefix_passes_through_without_defaults():
    spec = normalize_query("q top 20 recent")

    assert spec.kind == "q"
    assert spec.query == "top 20 recent"
    assert spec.expression == "top 20 recent"


def test_f_prefix_is_plain_text_now():
    kind, query = split_query("f spectral risk")
    spec = normalize_query("f spectral risk")

    assert kind == "raw"
    assert query == "f spectral risk"
    assert spec.expression == "# f spectral risk"


def test_empty_query_stays_empty():
    spec = normalize_query("")

    assert spec.kind == "raw"
    assert spec.query == ""
    assert spec.expression == ""
