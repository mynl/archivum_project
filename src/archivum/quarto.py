from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import pandas as pd
import logging
import math
import textwrap

from .bibtex import dict_to_bibtex
from .analytics.semantic import SEMANTIC_PALETTE
from .utilities import clean_latex

logger = logging.getLogger(__name__)

DEFAULT_CSL = '/s/TELOS/Biblio/journal-of-risk-and-uncertainty.csl'


def quick_abstract(text: str) -> str:
    """
    Try and find the abstract or summary in a text extract.
    """
    text_lower = text.lower()
    st = -1
    for kw in ['abstract', 'summary']:
        st = text_lower.find(kw)
        if st != -1:
            st += len(kw)
            break
    
    if st == -1:
        return ""
    
    ans = []
    lines = text[st:(st+4000)].split('\n')
    for i in lines:
        line = i.strip()
        if not line: 
            if ans: break
            continue
        if len(line) > 30:
            ans.append(line)
        else:
            if ans: break

    out = ' '.join(ans)
    out = re.sub(r'Further reproduction prohibited without permission\. ?|Reproduced with permission of the copyright owner\. ?', '', out)
    return out.strip()


def sanitize_for_latex(text: str) -> str:
    """
    Sanitize text for LaTeX/Tectonic consumption.
    """
    if not isinstance(text, str):
        return ""

    swaps = {
        '≤': '<=', '≥': '>=', '∈': 'in', '∉': 'not in',
        '≠': '!=', '≈': '~', '±': '+/-', '∞': 'inf',
        'π': 'pi', '→': '->', '←': '<-', '∑': 'sum', '∏': 'prod'
    }
    for char, replacement in swaps.items():
        text = text.replace(char, replacement)

    return "".join(ch for ch in text if ord(ch) >= 32 or ch in '\n\r\t')


def _markdown_link(label: object, target: object, *, target_blank: bool = True) -> str:
    clean_label = str(label or "").replace("[", "\\[").replace("]", "\\]")
    clean_target = str(target or "").replace("\\", "/").replace(")", "%29")
    attrs = '{target="_blank"}' if target_blank else ""
    return f"[{clean_label}](<{clean_target}>){attrs}"


def _markdown_table_cell(value: object) -> str:
    text = clean_latex(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def _markdown_pipe_table(headers: list[str], alignments: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(alignments) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_table_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def format_qmd_reference_line(lib: object, row: pd.Series, paras: list[int] | None = None, abstract: bool = True, web_links: bool = False) -> str:
    """
    Format a single reference line for QMD output.
    """
    tag = row.get("tag", "Unknown")
    title = sanitize_for_latex(str(row.get("title", ""))).strip("{}")
    author = sanitize_for_latex(str(row.get("author", "")))
    year = str(row.get("year", ""))

    paras_str = f" (paras: {', '.join(str(i) for i in paras)})" if paras else ""
    title_part = f"*{title}*" if title else ""

    meta_parts = []
    if author: meta_parts.append(author)
    if year: meta_parts.append(year)
    meta = ", ".join(meta_parts).strip()
    if meta: meta = f", {meta}"

    # Document link - the Tag is now the link
    if web_links:
        tag_link = f"**{_markdown_link(tag, f'/view/{tag}')}**"
    else:
        doc_path = None
        if hasattr(row, "path") and pd.notna(row.path):
            doc_path = lib.abspath(row.path)
        
        if doc_path and doc_path.exists():
            tag_link = f"**{_markdown_link(tag, doc_path)}**"
        else:
            tag_link = f'**{tag}**'

    # No bullets, just a paragraph starting with bold tag
    line = f"{tag_link} [@{tag}], {title_part}{meta}{paras_str}."
    
    if abstract:
        text_file = None
        p_str = str(row.get('path', ''))
        
        if p_str and p_str != 'nan':
            tf = lib.textpath(p_str)
            if tf.exists():
                text_file = tf
            else:
                if p_str.startswith(('/', '\\')):
                    tf = lib.textpath(p_str[1:])
                    if tf.exists(): text_file = tf
                
                if not text_file and 'hash' in row:
                    h_small = str(row['hash'])
                    if len(h_small) < 64:
                        matches = lib.doc_df[lib.doc_df.hash.str.startswith(h_small)]
                        if not matches.empty:
                            full_path = matches.iloc[0].path
                            tf = lib.textpath(full_path)
                            if tf.exists(): text_file = tf

        if text_file and row.get('type') != "book":
            try:
                txt = text_file.read_text(encoding='utf-8')
                abs_txt = quick_abstract(txt)
                if abs_txt:
                    abs_txt = sanitize_for_latex(abs_txt)
                    # No leading 4 spaces (which triggers code blocks)
                    line += f"\n\n> {abs_txt}"
            except Exception as e:
                logger.debug(f"Failed to read/extract abstract for {tag}: {e}")
    
    return line


def build_qmd_header(title: str, bibtex_file: str, csl_file: str = DEFAULT_CSL) -> str:
    """
    Build the YAML header for a QMD file.
    """
    lines = [
        "---",
        f"title: \"{title}\"",
        "author: archivum.export",
        f"bibliography: \"{Path(bibtex_file).as_posix()}\"",
        f"csl: \"{Path(csl_file).as_posix()}\"",
        "link-citations: true",
        "date-modified: last-modified",
        "format:",
        "  html:",
        "    theme: litera",
        "    smooth-scroll: true",
        "    citations-hover: true",
        "    page-layout: article",
        "    link-external-icon: true",
        "    link-external-newwindow: true",
        "    header-includes: |",
        "      <link rel=\"shortcut icon\" href=\"/static/icon/favicon.ico\">",
        "---",
        "",
    ]
    return "\n".join(lines)


def build_studio_header(title: str, bibtex_file: str, csl_file: str = DEFAULT_CSL) -> str:
    """
    Build the YAML header for a Studio Report QMD file.
    """
    lines = [
        "---",
        f"title: \"{title}\"",
        "author: \"archivum.report\"",
        "date: last-modified",
        f"bibliography: \"{Path(bibtex_file).as_posix()}\"",
        f"csl: \"{Path(csl_file).as_posix()}\"",
        "link-citations: true",
        "format:",
        "  pdf:",
        "    pdf-engine: tectonic",
        "    documentclass: scrartcl",
        "    papersize: a4",
        "    fontsize: 10pt",
        "    citeproc: true",
        "---",
        "",
    ]
    return "\n".join(lines)


def generate_qmd_report(lib: object, df: pd.DataFrame, out_path: Path, 
                        title: str = "Archivum Query Extract",
                        intro_text: str = "",
                        include_abstract: bool = True, 
                        query: str = "", 
                        web_links: bool = False):
    """
    Generate a Studio-compatible QMD report from a DataFrame.
    """
    if df.empty:
        out_path.write_text("No results found.", encoding="utf-8")
        return

    pdf = df.copy()
    if 'tag' not in pdf.columns:
        pdf['tag'] = "Unknown"

    if 'path' not in pdf.columns and 'tag' in pdf.columns:
        try:
            extra_info = lib.ref_doc_df.merge(lib.doc_df, on=["hash", "version"], how="inner")
            merge_cols = ["tag", "path"]
            if "hash" not in pdf.columns:
                merge_cols.append("hash")
            pdf = pdf.merge(extra_info[merge_cols], on="tag", how="left")
        except Exception as e:
            logger.warning(f"Failed to merge file info for report: {e}")

    sort_cols = []
    if 'author' in pdf.columns:
        def get_sort_author(s):
            if not isinstance(s, str) or not s: return ""
            return s.split(' and ')[0].split(',')[0].strip("{}")
        pdf['_sort_author'] = pdf['author'].apply(get_sort_author)
        sort_cols.append('_sort_author')
    
    if 'year' in pdf.columns: sort_cols.append('year')
    if 'tag' in pdf.columns: sort_cols.append('tag')
    if sort_cols: pdf = pdf.sort_values(sort_cols)

    bib_file = lib.config.bibtex_file
    csl_file = getattr(lib.config, 'csl_file', DEFAULT_CSL)

    header = build_studio_header(title, bib_file, csl_file)
    lines = [header]
    
    if intro_text:
        lines.append("# Introduction")
        lines.append(intro_text)
        lines.append("")

    if query:
        lines.append("## Query")
        lines.append(f"`{query}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## References")
    lines.append("")

    for _, row in pdf.iterrows():
        lines.append(format_qmd_reference_line(lib, row, abstract=include_abstract, web_links=web_links))
        lines.append("")
        
    lines.append("")
    lines.append("## Bibliography")
    lines.append("")
    lines.append("::: {#refs}")
    lines.append(":::")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def _report_asset_name(out_path: Path, suffix: str) -> str:
    return f"{out_path.stem}-{suffix}.svg"


def _report_asset_link(asset_name: str) -> str:
    return f"/reports/asset/{asset_name}"


def _wrap_label(text: object, width: int = 18, max_lines: int = 3) -> str:
    clean = clean_latex(str(text or "")).strip()
    if not clean:
        return ""
    lines = textwrap.wrap(clean, width=width, break_long_words=False, break_on_hyphens=False)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "..."
    return "\n".join(lines)


def _save_figure(fig, path: Path) -> None:
    fig.savefig(path, format="svg", bbox_inches="tight")
    fig.clear()


def _prepare_report_rows(lib: object, df: pd.DataFrame) -> pd.DataFrame:
    pdf = df.copy()
    if "tag" not in pdf.columns:
        pdf["tag"] = "Unknown"
    if "path" not in pdf.columns and "tag" in pdf.columns:
        try:
            extra_info = lib.ref_doc_df.merge(lib.doc_df, on=["hash", "version"], how="inner")
            merge_cols = ["tag", "path"]
            if "hash" not in pdf.columns:
                merge_cols.append("hash")
            pdf = pdf.merge(extra_info[merge_cols], on="tag", how="left")
        except Exception as e:
            logger.warning(f"Failed to merge file info for report: {e}")
    return pdf


def _sort_report_rows(df: pd.DataFrame) -> pd.DataFrame:
    pdf = df.copy()
    sort_cols = []
    if "author" in pdf.columns:
        def get_sort_author(s):
            if not isinstance(s, str) or not s:
                return ""
            return s.split(" and ")[0].split(",")[0].strip("{}")
        pdf["_sort_author"] = pdf["author"].apply(get_sort_author)
        sort_cols.append("_sort_author")
    if "year" in pdf.columns:
        sort_cols.append("year")
    if "tag" in pdf.columns:
        sort_cols.append("tag")
    if sort_cols:
        pdf = pdf.sort_values(sort_cols)
    return pdf.drop(columns=["_sort_author"], errors="ignore")


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set((float(x), float(y)) for x, y in points))
    if len(unique) <= 2:
        return unique

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _semantic_point_records(result) -> list[dict]:
    records = []
    for i, (_, row_idx) in enumerate(result.relevant_idx.iterrows()):
        matches = result.result_df[result.result_df.hash.astype(str) == str(row_idx.hash)]
        if matches.empty:
            continue
        meta = matches.iloc[0]
        cid = int(result.cluster_labels[i])
        cluster_number = ""
        for cs in result.cluster_summary:
            if cs["id"] == cid:
                cluster_number = cs["number"]
                break
        records.append(
            {
                "hash": str(row_idx.hash),
                "x": float(result.coords[i][0]),
                "y": float(result.coords[i][1]),
                "cluster_id": cid,
                "cluster_number": cluster_number,
                "cluster_name": result.cluster_themes.get(cid, "Lone Star"),
                "color": SEMANTIC_PALETTE[cid % len(SEMANTIC_PALETTE)] if cid >= 0 else "#adb5bd",
                "tag": str(meta.get("tag", "")),
                "title": clean_latex(str(meta.get("title", ""))),
                "author": str(meta.get("author", "")),
                "year": str(meta.get("year", "")),
            }
        )
    return records


def _plot_semantic_hulls(result, out_path: Path) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    records = _semantic_point_records(result)
    asset_name = _report_asset_name(out_path, "semantic-hulls")
    fig_path = out_path.parent / asset_name
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.set_facecolor("#f8f9fa")
    ax.axis("off")

    for cluster in result.cluster_summary:
        cid = int(cluster["id"])
        pts = [(r["x"], r["y"]) for r in records if r["cluster_id"] == cid]
        if not pts:
            continue
        color = cluster["color"]
        if len(pts) >= 3:
            hull = _convex_hull(pts)
            patch = Polygon(hull, closed=True, facecolor=color, edgecolor=color, alpha=0.16, linewidth=2.0)
            ax.add_patch(patch)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, s=18, color=color, alpha=0.65, linewidths=0)
        ax.text(
            sum(xs) / len(xs),
            sum(ys) / len(ys),
            _wrap_label(f"{cluster['number']}: {cluster['name']}", width=15, max_lines=3),
            ha="center",
            va="center",
            fontsize=6.5,
            fontweight="normal",
            color="#000000",
            linespacing=1.1,
        )

    ax.set_title("Semantic Cluster Overview", fontsize=12, fontweight="bold", color="#000000")
    _save_figure(fig, fig_path)
    plt.close(fig)
    return asset_name


def _plot_semantic_galaxy(result, out_path: Path) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    records = _semantic_point_records(result)
    asset_name = _report_asset_name(out_path, "semantic-galaxy")
    fig_path = out_path.parent / asset_name
    fig, ax = plt.subplots(figsize=(11, 8))
    ax.set_facecolor("#f8f9fa")
    ax.axis("off")

    background = [r for r in records if r["cluster_id"] < 0]
    if background:
        collection = ax.scatter(
            [r["x"] for r in background],
            [r["y"] for r in background],
            s=22,
            color="#adb5bd",
            alpha=0.45,
            label="Lone Star",
            linewidths=0,
        )
        try:
            collection.set_urls([f"/view/{r['tag']}" for r in background])
        except Exception:
            pass

    for cluster in result.cluster_summary:
        cid = int(cluster["id"])
        pts = [r for r in records if r["cluster_id"] == cid]
        if not pts:
            continue
        collection = ax.scatter(
            [r["x"] for r in pts],
            [r["y"] for r in pts],
            s=34,
            color=cluster["color"],
            alpha=0.82,
            label=f"{cluster['number']}: {cluster['name']}",
            linewidths=0.3,
            edgecolors="white",
        )
        try:
            collection.set_urls([f"/view/{r['tag']}" for r in pts])
        except Exception:
            pass
        for row in pts[:8]:
            label = _wrap_label(row["tag"], width=10, max_lines=1)
            ax.text(
                row["x"],
                row["y"],
                label,
                fontsize=4.5,
                color="#000000",
                alpha=0.92,
                linespacing=1.05,
                bbox={"boxstyle": "round,pad=0.16", "facecolor": "white", "edgecolor": "none", "alpha": 0.72},
            )

    ax.set_title("Semantic Galaxy Map", fontsize=12, fontweight="bold", color="#000000")
    _save_figure(fig, fig_path)
    plt.close(fig)
    return asset_name


def _plot_social_network(result, out_path: Path) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    asset_name = _report_asset_name(out_path, "social-network")
    fig_path = out_path.parent / asset_name
    graph = nx.Graph()
    for node in result.nodes:
        data = node.get("data", {})
        graph.add_node(data.get("id"), **data)
    for (source, target), edge in result.edges.items():
        graph.add_edge(source, target, weight=int(edge.get("weight", 1)), papers=edge.get("papers", []))

    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_facecolor("#f8f9fa")
    ax.axis("off")
    if graph.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "No social graph data", ha="center", va="center", transform=ax.transAxes)
        _save_figure(fig, fig_path)
        plt.close(fig)
        return asset_name

    pos = nx.spring_layout(graph, seed=42, k=1 / math.sqrt(max(1, graph.number_of_nodes())))
    weights = [graph.nodes[n].get("weight", 1) for n in graph.nodes]
    node_sizes = [120 + min(1250, w * 70) for w in weights]
    edge_widths = [0.7 + min(5.0, graph.edges[e].get("weight", 1) * 0.7) for e in graph.edges]
    nx.draw_networkx_edges(graph, pos, ax=ax, width=edge_widths, edge_color="#adb5bd", alpha=0.38)
    nx.draw_networkx_nodes(
        graph,
        pos,
        ax=ax,
        node_size=node_sizes,
        node_color="#0d6efd",
        alpha=0.78,
        linewidths=1.0,
        edgecolors="white",
    )
    labels = {n: _wrap_label(n, width=11, max_lines=5) for n in graph.nodes}
    nx.draw_networkx_labels(
        graph,
        pos,
        labels=labels,
        ax=ax,
        font_size=5.4,
        font_color="#000000",
    )
    ax.set_title("Social Network", fontsize=12, fontweight="bold", color="#000000")
    _save_figure(fig, fig_path)
    plt.close(fig)
    return asset_name


def _cluster_word_list(result, cluster_id: int, limit: int = 14) -> str:
    cluster_hashes = [
        str(result.relevant_idx.iloc[i].hash)
        for i, label in enumerate(result.cluster_labels)
        if int(label) == cluster_id
    ]
    if not cluster_hashes:
        return ""
    titles = result.result_df[result.result_df.hash.astype(str).isin(cluster_hashes)].title.dropna().astype(str)
    words = []
    stop_words = {
        "the", "and", "for", "with", "from", "that", "this", "into", "using",
        "based", "model", "models", "paper", "study", "analysis", "approach",
        "results", "data", "risk", "risks",
    }
    for title in titles:
        tokens = re.findall(r"\b[a-z][a-z-]{3,}\b", clean_latex(title).lower())
        words.extend([w for w in tokens if w not in stop_words])
    counts = pd.Series(words).value_counts()
    return ", ".join(counts.head(limit).index.astype(str).tolist())


def _cluster_summary_markdown(result) -> str:
    rows = []
    for cluster in result.cluster_summary:
        samples = "; ".join(clean_latex(str(s)) for s in cluster.get("samples", []))
        rows.append(
            [
                cluster["number"],
                cluster["name"],
                cluster["count"],
                samples,
            ]
        )
    return _markdown_pipe_table(
        ["Cluster", "Theme", "Papers", "Representative samples"],
        [":--------", ":------", "-------:", ":-----------------------"],
        rows,
    )


def _cluster_description_markdown(result) -> str:
    rows = []
    for cluster in result.cluster_summary:
        description = _cluster_word_list(result, int(cluster["id"])) or ""
        rows.append(
            [
                cluster["number"],
                cluster["name"],
                cluster["count"],
                description,
            ]
        )
    return _markdown_pipe_table(
        ["Cluster", "Theme", "Papers", "Expanded description"],
        [":--------", ":------", "-------:", ":---------------------"],
        rows,
    )


def generate_semantic_qmd_report(
    lib: object,
    result,
    out_path: Path,
    *,
    title: str = "Archivum Semantic Report",
    intro_text: str = "",
    include_abstract: bool = True,
    query: str = "",
    web_links: bool = False,
) -> None:
    if result.result_df.empty:
        out_path.write_text("No results found.", encoding="utf-8")
        return

    pdf = _prepare_report_rows(lib, result.result_df)
    hash_to_cluster = {}
    for i, (_, row) in enumerate(result.relevant_idx.iterrows()):
        hash_to_cluster[str(row.hash)] = int(result.cluster_labels[i])
    pdf["_cluster_id"] = pdf["hash"].astype(str).map(hash_to_cluster).fillna(-1).astype(int)

    hull_asset = _plot_semantic_hulls(result, out_path)
    galaxy_asset = _plot_semantic_galaxy(result, out_path)

    header = build_studio_header(title, lib.config.bibtex_file, getattr(lib.config, "csl_file", DEFAULT_CSL))
    lines = [header]
    if intro_text:
        lines.extend(["# Introduction", intro_text, ""])
    lines.extend([
        "## Analysis",
        f"- Query: `{query}`",
        f"- Semantic source: `{result.source_type}`",
        f"- Matched papers: {len(result.result_df)}",
        f"- Rendered papers: {len(result.relevant_idx)}",
        f"- Omitted papers: {len(result.omitted_hashes)}",
        f"- Clusters: {len(result.cluster_summary)}",
        "",
        "## Visualizations",
        "",
        f"![Semantic cluster overview]({_report_asset_link(hull_asset)})",
        "",
        f"![Semantic galaxy map]({_report_asset_link(galaxy_asset)})",
        "",
        "## Cluster Summary",
        "",
    ])
    lines.append(_cluster_summary_markdown(result))
    lines.extend(["", "## Cluster Description", ""])
    lines.append(_cluster_description_markdown(result))
    lines.extend(["", "---", "", "## References", ""])

    ordered_clusters = [(int(c["id"]), f"{c['number']}: {c['name']}") for c in result.cluster_summary]
    ordered_clusters.append((-1, "Lone Star"))
    for cid, heading in ordered_clusters:
        group = pdf[pdf["_cluster_id"] == cid]
        if group.empty:
            continue
        lines.extend([f"### {heading}", ""])
        word_list = _cluster_word_list(result, cid)
        if word_list:
            lines.extend([f"**Related terms:** {word_list}", ""])
        for _, row in _sort_report_rows(group).iterrows():
            lines.append(format_qmd_reference_line(lib, row, abstract=include_abstract, web_links=web_links))
            lines.append("")

    lines.extend(["", "## Bibliography", "", "::: {#refs}", ":::", ""])
    out_path.write_text("\n".join(lines), encoding="utf-8")


def generate_social_qmd_report(
    lib: object,
    result,
    out_path: Path,
    *,
    title: str = "Archivum Social Network Report",
    intro_text: str = "",
    include_abstract: bool = True,
    query: str = "",
    web_links: bool = False,
) -> None:
    if result.result_df.empty:
        out_path.write_text("No results found.", encoding="utf-8")
        return

    social_asset = _plot_social_network(result, out_path)
    pdf = _sort_report_rows(_prepare_report_rows(lib, result.result_df))
    top_authors = sorted(
        [n.get("data", {}) for n in result.nodes],
        key=lambda d: int(d.get("weight", 0)),
        reverse=True,
    )[:20]
    top_edges = sorted(
        [(source, target, data) for (source, target), data in result.edges.items()],
        key=lambda item: int(item[2].get("weight", 0)),
        reverse=True,
    )[:20]

    header = build_studio_header(title, lib.config.bibtex_file, getattr(lib.config, "csl_file", DEFAULT_CSL))
    lines = [header]
    if intro_text:
        lines.extend(["# Introduction", intro_text, ""])
    lines.extend([
        "## Analysis",
        f"- Query: `{query}`",
        f"- Papers: {len(result.result_df)}",
        f"- Authors: {len(result.nodes)}",
        f"- Collaborations: {len(result.edges)}",
        "",
        "## Social Network",
        "",
        f"![Social network]({_report_asset_link(social_asset)})",
        "",
        "## Top Authors",
        "",
        "| Author | Papers |",
        "|---|---:|",
    ])
    for author in top_authors:
        lines.append(
            f"| {_markdown_table_cell(author.get('label', author.get('id', '')))} | {_markdown_table_cell(author.get('weight', 0))} |"
        )
    lines.extend(["", "## Top Collaborations", "", "| Authors | Shared papers |", "|---|---:|"])
    for source, target, edge in top_edges:
        lines.append(
            f"| {_markdown_table_cell(f'{source} / {target}')} | {_markdown_table_cell(edge.get('weight', 0))} |"
        )
    lines.extend(["", "---", "", "## References", ""])
    for _, row in pdf.iterrows():
        lines.append(format_qmd_reference_line(lib, row, abstract=include_abstract, web_links=web_links))
        lines.append("")
    lines.extend(["", "## Bibliography", "", "::: {#refs}", ":::", ""])
    out_path.write_text("\n".join(lines), encoding="utf-8")


@dataclass(slots=True)
class QmdParser:
    """
    Parse a Quarto .qmd file (UTF-8).
    """
    path: Path

    all_text: str = ""
    header: str = ""
    text_paras: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    tags_sorted: list[str] = field(default_factory=list)

    _include_re: re.Pattern[str] = field(
        default=re.compile(r"\{\{<\s*include\s+([^>]+?)\s*>}}"),
        init=False,
        repr=False,
    )
    _code_fence_re: re.Pattern[str] = field(
        default=re.compile(
            r"^```[^\n]*\n.*?^```[ \t]*\n?",
            flags=re.MULTILINE | re.DOTALL,
        ),
        init=False,
        repr=False,
    )
    _html_comment_re: re.Pattern[str] = field(
        default=re.compile(r"<!--.*?-->", flags=re.DOTALL),
        init=False,
        repr=False,
    )
    _yaml_frontmatter_re: re.Pattern[str] = field(
        default=re.compile(r"(?s)\A---[ \t]*\n(.*?)\n---[ \t]*\n?"),
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.all_text = self._expand_includes(self.path, seen=set())
        no_code = self._strip_code_blocks(self.all_text)
        no_code_no_comments = self._strip_comments(no_code)
        body = self._extract_yaml_header(no_code_no_comments)
        self.text_paras = self._split_paragraphs_keep_divs(body)

    def to_dict(self) -> dict[str, object]:
        return {
            "all_text": self.all_text,
            "header": self.header,
            "text_paras": self.text_paras,
            "code_blocks": self.code_blocks,
            "comments": self.comments,
        }

    def _expand_includes(self, path: Path, seen: set[Path]) -> str:
        path = path.resolve()
        if path in seen: return f"\n<!-- include cycle detected: {path.as_posix()} -->\n"
        if not path.exists(): return f"\n<!-- include missing: {path.as_posix()} -->\n"
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        def repl(m: re.Match[str]) -> str:
            raw = m.group(1).strip()
            if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                raw = raw[1:-1].strip()
            inc_path = (path.parent / raw).resolve()
            return self._expand_includes(inc_path, seen)
        expanded = self._include_re.sub(repl, text)
        seen.remove(path)
        return expanded

    def _strip_code_blocks(self, text: str) -> str:
        self.code_blocks.clear()
        def repl(m: re.Match[str]) -> str:
            self.code_blocks.append(m.group(0))
            return "\n"
        return self._code_fence_re.sub(repl, text)

    def _strip_comments(self, text: str) -> str:
        self.comments.clear()
        def repl(m: re.Match[str]) -> str:
            self.comments.append(m.group(0))
            return "\n"
        return self._html_comment_re.sub(repl, text)

    def _extract_yaml_header(self, text: str) -> str:
        self.header = ""
        m = self._yaml_frontmatter_re.match(text)
        if not m: return text
        self.header = m.group(1)
        return text[m.end():]

    @staticmethod
    def _split_paragraphs_keep_divs(text: str) -> list[str]:
        lines = text.splitlines()
        paras: list[str] = []
        buf: list[str] = []
        def flush_buf() -> None:
            if not buf: return
            para = "\n".join(buf).strip()
            if para: paras.append(para)
            buf.clear()
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if line.lstrip().startswith(":::"):
                flush_buf()
                div_lines = [line]
                i += 1
                while i < n:
                    div_lines.append(lines[i])
                    if lines[i].lstrip().startswith(":::"):
                        i += 1
                        break
                    i += 1
                div_para = "\n".join(div_lines).strip()
                if div_para: paras.append(div_para)
                continue
            if line.strip() == "":
                flush_buf()
                i += 1
                continue
            buf.append(line)
            i += 1
        flush_buf()
        return paras

    def citations(self) -> list[str]:
        cite_rex = re.compile(r"(?<!@)@(?!REF)([A-Z][A-Za-z0-9]+)")
        return sorted(set([m.group(1) for m in cite_rex.finditer(self.all_text)]))

    def ref_summary(self, out_path: Path, lib: object, *,
                    csl_value: str = DEFAULT_CSL, execute: bool = False,
                    abstract: bool = True) -> list[str]:
        src = self.path.resolve()
        out_path = Path(out_path).resolve()
        actions: list[str] = []

        if out_path in src.parents: raise ValueError(f"out_path is parent of source")
        if not out_path.exists():
            actions.append(f"mkdir {out_path}")
            if execute: out_path.mkdir(parents=True, exist_ok=True)

        self._check_dir_structure(out_path)
        actions.extend(self._clear_dir(out_path, execute=execute))

        cite_rex = re.compile(r"(?<!@)@(?!REF\b)([A-Z][A-Za-z0-9]+)")
        tag_to_paras: dict[str, list[int]] = {}
        for i, para in enumerate(self.text_paras):
            for tag in [m.group(1) for m in cite_rex.finditer(para)]:
                tag_to_paras.setdefault(tag, []).append(i)

        items_sorted = sorted(tag_to_paras.items(), key=lambda kv: min(kv[1]))
        tags_sorted_dict = dict(items_sorted)

        out_qmd = (out_path / src.name).resolve()
        if out_qmd == src: raise ValueError(f"output .qmd would overwrite source")

        header = build_qmd_header("Reference Summary", lib.config.bibtex_file, csl_value)
        lines = [header, "## References", ""]

        df = lib.database
        for tag, paras in tags_sorted_dict.items():
            row = df[df["tag"] == tag]
            if row.empty:
                lines.append(f"**{tag}** [@{tag}]: **Missing in database**")
                continue
            lines.append(format_qmd_reference_line(lib, row.iloc[0], paras=paras, abstract=abstract))
            lines.append("")

        if execute:
            out_qmd.write_text("\n".join(lines), encoding="utf-8")
        actions.append(f"write {out_qmd}")
        return actions

    def _check_dir_structure(self, out_path: Path) -> None:
        if not out_path.exists(): return
        if len(list(out_path.glob("*.qmd"))) > 1: raise ValueError("out_path has >1 .qmd files")
        allowed = {".pdf", ".html", ".htm", ".tex", ".log", ".aux", ".toc", ".out", ".synctex", ".synctex.gz", ".json", ".xml", ".png", ".jpg", ".jpeg", ".svg", ".css", ".js"}
        for p in out_path.iterdir():
            if p.is_dir() and not p.name.endswith('_files'): raise ValueError(f"unexpected dir {p.name}")
            if not (p.is_dir() or p.suffix.lower() == ".qmd" or p.is_symlink() or p.suffix.lower() in allowed):
                raise ValueError(f"unexpected file {p.name}")

    def _clear_dir(self, out_path: Path, execute: bool) -> list[str]:
        actions: list[str] = []
        if not out_path.exists(): return actions
        for p in out_path.iterdir():
            actions.append(f"rm {p}")
            if execute:
                try: p.unlink()
                except: pass
        return actions

    def generate_bibtex(self, lib: object, out_file: Path) -> int:
        tags = self.citations()
        if not tags: return 0
        df = lib.ref_df
        matches = df[df['tag'].isin(tags)]
        if matches.empty: return 0
        bib_entries = [dict_to_bibtex(row) for _, row in matches.sort_values("tag").iterrows()]
        out_file.write_text("\n\n".join(bib_entries), encoding="utf-8")
        return len(matches)
