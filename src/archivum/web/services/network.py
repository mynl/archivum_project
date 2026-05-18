from ...analytics.networks import analyze_social_network
from ...analytics.semantic import analyze_semantic


def empty_social_graph_response():
    return {"nodes": [], "edges": [], "elements": [], "papers": 0, "clusters": []}


def empty_semantic_graph_response():
    return {"elements": [], "papers": 0, "clusters": []}


def get_social_network_payload(
    lib,
    raw_query: str,
    verbosity: str = "verbose",
    *,
    case_sensitive: bool = False,
) -> dict:
    if not raw_query:
        return empty_social_graph_response()

    result = analyze_social_network(lib, raw_query, case_sensitive=case_sensitive)
    return result.to_cytoscape_json(verbosity=verbosity)


def get_semantic_network_payload(
    lib,
    raw_query: str,
    source_type: str = "title",
    verbosity: str = "verbose",
    *,
    case_sensitive: bool = False,
) -> dict:
    if not raw_query:
        return empty_semantic_graph_response()

    result = analyze_semantic(lib, raw_query, source_type, case_sensitive=case_sensitive)
    if result.result_df.empty:
        return result.to_cytoscape_json(verbosity=verbosity)
    if result.relevant_idx.empty:
        payload = {
            "elements": [],
            "papers": 0,
            "omitted_count": len(result.omitted_hashes),
            "omitted_reason": "No text extracts found.",
            "clusters": [],
            "embedded_count": result.embedded_count,
            "cached_embedding_count": result.cached_embedding_count,
            "embedding_work_count": result.embedding_work_count,
            "embedding_work_pending": result.embedding_work_count > 0,
        }
        if verbosity == "verbose":
            payload["log_messages"] = result.to_cytoscape_json(verbosity=verbosity).get("log_messages", [])
        return payload
    return result.to_cytoscape_json(verbosity=verbosity)
