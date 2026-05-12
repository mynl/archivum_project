from dataclasses import dataclass, field
import itertools
import logging

import pandas as pd

from ..search.universe import resolve_universe
from ..utilities import clean_latex


logger = logging.getLogger(__name__)


EMPTY_SOCIAL_GRAPH = {"nodes": [], "edges": [], "elements": [], "papers": 0, "clusters": []}


@dataclass
class SocialNetworkResult:
    result_df: pd.DataFrame
    nodes: list[dict] = field(default_factory=list)
    edges: dict[tuple[str, str], dict] = field(default_factory=dict)
    elements: list[dict] = field(default_factory=list)
    hashes: str = ""
    clusters: list[dict] = field(default_factory=list)

    def to_cytoscape_json(self, *, verbosity: str = "minimal") -> dict:
        if self.result_df.empty:
            return EMPTY_SOCIAL_GRAPH.copy()

        return {
            "nodes": self.nodes,
            "elements": self.elements,
            "papers": len(self.result_df),
            "hashes": self.hashes,
            "status_msg": (
                f'<i class="bi bi-people me-2"></i> '
                f"Social graph built for {len(self.result_df)} papers."
            ),
            "clusters": self.clusters,
        }


def analyze_social_network(lib, raw_query: str) -> SocialNetworkResult:
    df = lib.database
    universe_hashes = resolve_universe(lib, raw_query)
    result_df = df[df["hash"].astype(str).isin(universe_hashes)]

    if result_df.empty:
        return SocialNetworkResult(result_df=result_df)

    nodes, edges = _build_social_graph(result_df)
    elements = nodes + [
        {"data": {"source": k[0], "target": k[1], "weight": int(v["weight"]), "papers": v["papers"]}}
        for k, v in edges.items()
    ]

    hash_list = result_df["hash"].dropna().astype(str).str[:8].unique()[:500]
    hashes = "|".join([str(h) for h in hash_list])

    return SocialNetworkResult(
        result_df=result_df,
        nodes=nodes,
        edges=edges,
        elements=elements,
        hashes=hashes,
    )


def _build_social_graph(result_df: pd.DataFrame) -> tuple[list[dict], dict[tuple[str, str], dict]]:
    paper_to_authors = {}
    author_to_papers = {}

    for _, row in result_df.iterrows():
        authors_raw = row.get("author")
        if pd.isna(authors_raw):
            continue

        author_list = [_normalize_name(a) for a in str(authors_raw).split(" and ") if a.strip()]
        author_list = [a for a in author_list if a != "Unknown"]
        if not author_list:
            continue

        paper_info = {
            "title": clean_latex(str(row.get("title", "Unknown"))),
            "year": str(row.get("year", "9999")).split(".")[0],
            "tag": str(row.tag),
        }
        for auth in author_list:
            author_to_papers.setdefault(auth, []).append(paper_info)
        paper_to_authors[str(row.tag)] = (author_list, paper_info)

    nodes = [
        {
            "data": {
                "id": str(auth),
                "label": str(auth),
                "weight": int(len(papers)),
                "papers": papers[:50],
            }
        }
        for auth, papers in author_to_papers.items()
    ]

    edges = {}
    for _tag, (author_list, paper_info) in paper_to_authors.items():
        if len(author_list) < 2:
            continue
        for a1, a2 in itertools.combinations(sorted(author_list), 2):
            key = (str(a1), str(a2))
            if key not in edges:
                edges[key] = {"weight": 0, "papers": []}
            edges[key]["weight"] += 1
            edges[key]["papers"].append(paper_info)

    return nodes, edges


def _normalize_name(name) -> str:
    if pd.isna(name):
        return "Unknown"
    s = str(name).strip()
    if not s or s.lower() == "nan" or s.lower() == "unknown":
        return "Unknown"
    return s.rstrip(".").strip()
