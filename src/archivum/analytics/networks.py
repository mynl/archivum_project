from dataclasses import dataclass, field
import itertools
import logging

import pandas as pd

from ..search.universe import resolve_universe_details
from ..utilities import clean_latex
from .timing import PerformanceTimer, TimingEvent, timing_messages


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
    timings: list[TimingEvent] = field(default_factory=list)
    rg_command: str = ""
    rg_cache_hit: bool = False

    def to_cytoscape_json(self, *, verbosity: str = "minimal") -> dict:
        payload_timer = PerformanceTimer()
        if self.result_df.empty:
            payload = EMPTY_SOCIAL_GRAPH.copy()
            if verbosity == "verbose":
                payload_timer.mark("social payload serialization")
                payload["log_messages"] = timing_messages(self.timings + payload_timer.events)
            return payload

        payload = {
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
        if verbosity == "verbose":
            payload_timer.mark("social payload serialization")
            messages = [
                f"Social papers after query: {len(self.result_df)}",
                f"Social nodes: {len(self.nodes)}",
                f"Social edges: {len(self.edges)}",
                *timing_messages(self.timings + payload_timer.events),
            ]
            if self.rg_command:
                messages.insert(1, f"Ripgrep command: {self.rg_command}")
                messages.insert(2, f"Ripgrep cache: {'hit' if self.rg_cache_hit else 'miss'}")
            payload["log_messages"] = messages
        return payload


def analyze_social_network(lib, raw_query: str, *, case_sensitive: bool = False) -> SocialNetworkResult:
    timer = PerformanceTimer()
    df = lib.database
    timer.mark("database load")
    universe_result = resolve_universe_details(lib, raw_query, case_sensitive=case_sensitive)
    universe_hashes = universe_result.hashes
    timer.mark("universe resolution")
    result_df = df[df["hash"].astype(str).isin(universe_hashes)]
    timer.mark("database filtering")

    if result_df.empty:
        timer.mark("total social analysis")
        return SocialNetworkResult(
            result_df=result_df,
            timings=timer.events,
            rg_command=universe_result.rg_command,
            rg_cache_hit=universe_result.rg_cache_hit,
        )

    nodes, edges = _build_social_graph(result_df)
    timer.mark("author graph build")
    elements = nodes + [
        {"data": {"source": k[0], "target": k[1], "weight": int(v["weight"]), "papers": v["papers"]}}
        for k, v in edges.items()
    ]
    timer.mark("cytoscape element build")

    hash_list = result_df["hash"].dropna().astype(str).str[:8].unique()[:500]
    hashes = "|".join([str(h) for h in hash_list])
    timer.mark("hash export list build")
    timer.mark("total social analysis")

    return SocialNetworkResult(
        result_df=result_df,
        nodes=nodes,
        edges=edges,
        elements=elements,
        hashes=hashes,
        timings=timer.events,
        rg_command=universe_result.rg_command,
        rg_cache_hit=universe_result.rg_cache_hit,
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
