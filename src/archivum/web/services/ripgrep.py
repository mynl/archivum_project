from dataclasses import dataclass
from pathlib import Path
import html
import logging
import re
import subprocess
import time

from flask import render_template
import pandas as pd

from ..cache import get_hash_meta_cache, get_rg_cache_item, set_rg_cache_item
from .exports import export_dataframe_to_csv, query_export_filename


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RipgrepSearchOptions:
    query: str = ""
    show_files: bool = False
    context_after: str = "0"
    context_before: str = "0"
    show_counts: bool = False
    show_summary: bool = False
    case_sensitive: bool = False
    glob1: str = ""
    glob2: str = ""
    filter_mode: str = "tagged"

    @classmethod
    def from_request_args(cls, args):
        show_summary = args.get("summary") == "true"
        show_counts = args.get("counts") == "true" or show_summary
        return cls(
            query=args.get("q", "").strip(),
            show_files=args.get("files") == "true",
            context_after=args.get("after", "0"),
            context_before=args.get("before", "0"),
            show_counts=show_counts,
            show_summary=show_summary,
            case_sensitive=args.get("case") == "sensitive",
            glob1=args.get("glob1", "").strip(),
            glob2=args.get("glob2", "").strip(),
            filter_mode=args.get("filter", "tagged"),
        )

    @property
    def has_work(self):
        return bool(self.query or self.show_files)

    @property
    def mode(self):
        if self.show_summary:
            return "summary"
        if self.show_counts:
            return "counts"
        return "details"

    @property
    def search_key(self):
        key = (
            f"{self.query}_{self.mode}_{self.filter_mode}_"
            f"{self.case_sensitive}_{self.glob1}_{self.glob2}"
        )
        if self.mode == "details":
            key += f"_{self.context_after}_{self.context_before}"
        return key

    @property
    def data_key(self):
        return f"{self.query}_{self.filter_mode}_{self.case_sensitive}_{self.glob1}_{self.glob2}"


def warm_ripgrep_cache(lib):
    """Build the metadata cache used by Ripgrep views."""
    get_hash_meta_cache(lib)


def get_cached_ripgrep_search(lib, options: RipgrepSearchOptions):
    if options.show_files:
        return None

    cached_html = get_rg_cache_item(lib, options.search_key, "html")
    stats_meta = get_rg_cache_item(lib, options.search_key, "stats")
    if not (cached_html and stats_meta):
        return None

    logger.info("RG HTML Cache hit for: %s (%s)", options.query, options.mode)
    matches = stats_meta.get("matches", 0)
    docs = stats_meta.get("docs", 0)
    hashes = stats_meta.get("hashes", "")
    verb = "Summarized" if options.mode in ["summary", "counts"] else "Found"
    noun = "documents" if options.mode in ["summary", "counts"] else "files"

    cache_tag = _render_oob(
        "rg-stats-header",
        _render_stats(
            verb=verb,
            matches=matches,
            docs=docs,
            noun=noun,
            cached=True,
        ),
    )

    export_oob = ""
    if hashes:
        h_list = [h[:8] for h in hashes.split("|")]
        export_oob = _render_export_button("rg", "|".join(h_list))

    status_fix = _render_status("(Retrieved from Cache)", margin_bottom="0.25rem")
    return cached_html + cache_tag + status_fix + export_oob


def stream_ripgrep_search(lib, options: RipgrepSearchOptions):
    start_time = time.time()
    html_buffer = []
    final_stats = {"matches": 0, "docs": 0}
    hash_prefix_to_meta = get_hash_meta_cache(lib)
    common_args = _build_common_args(options)

    def yield_and_buffer(chunk):
        html_buffer.append(chunk)
        return chunk

    yield yield_and_buffer(_render_oob("rg-results", ""))
    yield yield_and_buffer(_render_oob("rg-stats-header", ""))
    yield yield_and_buffer(_render_oob("rg-more-container", "", style="display: none;"))

    if options.show_files:
        yield from _stream_file_listing(lib, options, common_args, hash_prefix_to_meta, start_time, yield_and_buffer)
        return

    if options.show_counts:
        yield from _stream_counts_or_summary(
            lib,
            options,
            common_args,
            hash_prefix_to_meta,
            start_time,
            final_stats,
            html_buffer,
            yield_and_buffer,
        )
        return

    yield from _stream_details(
        lib,
        options,
        common_args,
        hash_prefix_to_meta,
        start_time,
        final_stats,
        html_buffer,
        yield_and_buffer,
    )


def export_ripgrep_csv(lib, query):
    export_df = ripgrep_export_dataframe(lib, query)
    if isinstance(export_df, tuple):
        return export_df

    return export_dataframe_to_csv(export_df, query_export_filename(query))


def ripgrep_export_dataframe(lib, query):
    """Return the full ripgrep export dataframe for CSV and BibTeX exports."""
    is_regex = any(c in query for c in r".*+?^$|()[]{}")
    args = ["-n", "-H"]
    if not is_regex:
        args.append("-F")
    elif any(p in query for p in ["(?=", "(?!", "(?<=", "(?<!"]):
        args.append("--pcre2")

    _rc, proc = lib.run_ripgrep(query, args)
    counts = {}
    for line in proc.stdout:
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        h_prefix = Path(parts[0]).name[:10]
        counts[h_prefix] = counts.get(h_prefix, 0) + 1

    if not counts:
        return "No matches found to export.", 400

    prefix_to_full = {}
    if not lib.database.empty:
        for _, row in lib.database[["name", "hash"]].iterrows():
            if pd.isna(row["name"]):
                continue
            prefix_to_full[str(row["name"])[:10]] = row["hash"]

    data_rows = []
    df = lib.database
    for h_prefix, count in counts.items():
        full_hash = prefix_to_full.get(h_prefix)
        if not full_hash:
            match = df[df["hash"].astype(str).str.startswith(h_prefix)]
        else:
            match = df[df["hash"] == full_hash]

        if match.empty:
            continue

        row = match.iloc[0].to_dict()
        row["matches"] = count
        data_rows.append(row)

    if not data_rows:
        return "Failed to map matches to database.", 500

    export_df = pd.DataFrame(data_rows)
    cols = ["tag", "author", "title", "year", "publisher", "journal", "type", "matches", "path", "hash"]
    existing_cols = [c for c in cols if c in export_df.columns]
    remaining = [c for c in export_df.columns if c not in existing_cols]
    return export_df[existing_cols + remaining]


def _render_export_button(id_prefix, hashes, input_id="rg-input"):
    return render_template(
        "components/export_button_active.html",
        id_prefix=id_prefix,
        hashes=hashes,
        input_id=input_id,
    )


def _render_oob(target_id, content, *, classes="", style="", swap="true"):
    return render_template(
        "components/rg_oob.html",
        target_id=target_id,
        content=content,
        classes=classes,
        style=style,
        swap=swap,
    )


def _render_status(command, *, margin_bottom="0.5rem"):
    return render_template(
        "components/rg_status.html",
        command=command,
        margin_bottom=margin_bottom,
    )


def _render_stats(
    *,
    verb="Found",
    matches=0,
    docs=0,
    noun="files",
    cached=False,
    total_time=None,
    rg_time=None,
    classes="mt-n3 mb-3",
    file_count=None,
):
    return render_template(
        "components/rg_stats.html",
        verb=verb,
        matches=matches,
        docs=docs,
        noun=noun,
        cached=cached,
        total_time=total_time,
        rg_time=rg_time,
        classes=classes,
        file_count=file_count,
    )


def _format_glob(glob):
    if not glob:
        return None
    if "*" in glob or "?" in glob or "[" in glob:
        return glob
    if "." in glob:
        return f"*{glob}*"
    return f"*{glob}*.md"


def _build_common_args(options):
    is_regex = any(c in options.query for c in r".*+?^$|()[]{}")
    common_args = []
    if not is_regex:
        common_args.append("-F")
    elif any(p in options.query for p in ["(?=", "(?!", "(?<=", "(?<!"]):
        common_args.append("--pcre2")

    g1 = _format_glob(options.glob1)
    if g1:
        common_args.extend(["--iglob", g1])
    g2 = _format_glob(options.glob2)
    if g2:
        common_args.extend(["--iglob", g2])
    if not (g1 or g2):
        common_args.extend(["-g", "*.md"])
    return common_args


def _stream_file_listing(lib, options, common_args, hash_prefix_to_meta, start_time, yield_and_buffer):
    cmd = ["rg", "--files"] + common_args + [str(lib.text_dir_path)]
    yield yield_and_buffer(_render_status(" ".join(cmd)))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        files = []
        for line in proc.stdout:
            h_prefix = Path(line.strip()).name[:10]
            meta = hash_prefix_to_meta.get(h_prefix, {})
            if options.filter_mode == "tagged" and not meta.get("tag"):
                continue
            files.append({"hash": h_prefix, "tag": meta.get("tag"), "title": meta.get("title", h_prefix)})
        yield yield_and_buffer(
            _render_oob("rg-results", render_template("components/rg_files.html", files=files))
        )
        stats_html = _render_stats(
            classes="mb-3",
            file_count=len(files),
            total_time=f"{time.time() - start_time:.3f}s",
        )
        yield yield_and_buffer(_render_oob("rg-stats-header", stats_html))
    except Exception as e:
        yield yield_and_buffer(_render_oob(None, f"Ripgrep Error: {html.escape(str(e))}", classes="error"))


def _stream_counts_or_summary(
    lib,
    options,
    common_args,
    hash_prefix_to_meta,
    start_time,
    final_stats,
    html_buffer,
    yield_and_buffer,
):
    try:
        counts = get_rg_cache_item(lib, options.data_key, "data")
        rg_internal_time = "0.000s"

        if counts is None:
            args = ["-n", "-H"] + common_args
            if not options.case_sensitive:
                args.append("-i")
            _rc, proc = lib.run_ripgrep(options.query, args)
            yield yield_and_buffer(_render_status(f'rg {" ".join(args)} "{options.query}"'))

            counts, stats_buffer, is_stats_section = {}, [], False
            for line in proc.stdout:
                if not line.strip():
                    continue
                if re.match(r"^\s*\d+ matches", line) or re.match(r"^\s*\d+ matched lines", line):
                    is_stats_section = True
                if is_stats_section:
                    stats_buffer.append(line)
                    continue
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                h_prefix = Path(parts[0]).name[:10]
                meta = hash_prefix_to_meta.get(h_prefix, {})
                if options.filter_mode == "tagged" and not meta.get("tag"):
                    continue
                counts[h_prefix] = counts.get(h_prefix, 0) + 1

            m = re.findall(r"(\d+\.\d+) seconds", "".join(stats_buffer))
            rg_internal_time = f"{float(m[-1]):.3f}s" if m else "0.000s"
            set_rg_cache_item(options.data_key, counts, "data")
        else:
            yield yield_and_buffer(_render_status("(Retrieved from Cache)"))

        counts_list = []
        total_m = 0
        for h_prefix, count_val in counts.items():
            meta = hash_prefix_to_meta.get(h_prefix, {})
            counts_list.append(
                {
                    "hash": h_prefix,
                    "count": count_val,
                    "tag": meta.get("tag"),
                    "title": meta.get("title", ""),
                    "authors": meta.get("authors", ""),
                }
            )
            total_m += count_val
        counts_list.sort(key=lambda x: x["count"], reverse=True)

        final_stats["matches"] = total_m
        final_stats["docs"] = len(counts_list)

        if options.show_summary:
            summary_html = _render_summary(counts, hash_prefix_to_meta, total_m)
            yield yield_and_buffer(_render_oob("rg-results", summary_html, classes="mt-5"))
        else:
            yield yield_and_buffer(
                _render_oob(
                    "rg-results",
                    render_template("components/rg_counts.html", counts=counts_list),
                    classes="mt-4",
                )
            )

        top_hashes = "|".join([x["hash"][:8] for x in counts_list[:500]])
        if top_hashes:
            yield yield_and_buffer(_render_export_button("rg", top_hashes))

        final_stats["matches"] = total_m
        final_stats["docs"] = len(counts_list)
        final_stats["hashes"] = top_hashes

        stats_html = _render_stats(
            verb="Summarized",
            matches=total_m,
            docs=len(counts_list),
            noun="documents",
            total_time=f"{time.time() - start_time:.3f}s",
            rg_time=rg_internal_time,
        )
        yield yield_and_buffer(_render_oob("rg-stats-header", stats_html))
        set_rg_cache_item(options.search_key, "".join(html_buffer), "html")
        set_rg_cache_item(options.search_key, final_stats, "stats")
    except Exception as e:
        logger.error("RG Summary/Counts Error: %s", e)
        import traceback

        logger.error(traceback.format_exc())
        yield yield_and_buffer(_render_oob(None, f"Ripgrep Summary Error: {html.escape(str(e))}", classes="error"))


def _render_summary(counts, hash_prefix_to_meta, total_m):
    year_data, author_data = {}, {}
    total_papers_set = set()
    for h_prefix, count_val in counts.items():
        meta = hash_prefix_to_meta.get(h_prefix, {})
        year, size = meta.get("year", "9999"), meta.get("size", 0)
        total_papers_set.add(h_prefix)

        if year not in year_data:
            year_data[year] = {"papers": 0, "matches": 0, "size": 0, "hashes": set()}
        year_data[year]["papers"] += 1
        year_data[year]["matches"] += count_val
        year_data[year]["size"] += size
        year_data[year]["hashes"].add(h_prefix[:8])

        raw_authors = meta.get("authors", "Unknown")
        author_list = [a.strip().replace("{", "").replace("}", "") for a in raw_authors.split(" and ")]
        for auth in author_list:
            if not auth or auth == "Unknown":
                continue
            if auth not in author_data:
                author_data[auth] = {"papers": 0, "matches": 0, "size": 0, "hashes": set()}
            author_data[auth]["papers"] += 1
            author_data[auth]["matches"] += count_val
            author_data[auth]["size"] += size
            author_data[auth]["hashes"].add(h_prefix[:8])

    total_papers_count = len(total_papers_set)

    def prepare_rows(data):
        if not data:
            return []
        max_matches = max(v["matches"] for v in data.values())
        rows = []
        for label, vals in data.items():
            hash_query = f"hash ~ /{ '|'.join(list(vals.get('hashes', []))[:50]) }/" if "hashes" in vals else ""
            rows.append(
                {
                    "label": label,
                    "papers": vals["papers"],
                    "papers_pct": (vals["papers"] / total_papers_count * 100),
                    "matches": vals["matches"],
                    "matches_pct": (vals["matches"] / total_m * 100),
                    "spark_pct": (vals["matches"] / max_matches * 100),
                    "mtc_pap": vals["matches"] / vals["papers"],
                    "mtc_100kb": (vals["matches"] / (vals["size"] / 102400)) if vals["size"] else 0,
                    "hash_query": hash_query,
                }
            )
        return rows

    year_rows = sorted(prepare_rows(year_data), key=lambda x: x["label"], reverse=True)
    author_rows = sorted(prepare_rows(author_data), key=lambda x: x["matches"], reverse=True)[:100]
    return render_template(
        "components/rg_summary.html",
        year_rows=year_rows,
        author_rows=author_rows,
        totals={"papers": total_papers_count, "matches": total_m, "author_count": len(author_data)},
    )


def _stream_details(
    lib,
    options,
    common_args,
    hash_prefix_to_meta,
    start_time,
    final_stats,
    html_buffer,
    yield_and_buffer,
):
    try:
        args = ["-n", "-H"] + common_args
        if not options.case_sensitive:
            args.append("-i")
        args.extend(["-A", options.context_after, "-B", options.context_before])
        _rc, proc = lib.run_ripgrep(options.query, args)
        yield yield_and_buffer(_render_status(f'rg {" ".join(args)} "{options.query}"'))

        limit, rendered_matches, total_matches, seen_hashes = 500, 0, 0, set()
        current_block, last_file, stats_buffer, is_stats_section = None, None, [], False

        for line in proc.stdout:
            if not line.strip():
                continue
            if re.match(r"^\s*\d+ matches", line) or re.match(r"^\s*\d+ matched lines", line):
                is_stats_section = True
            if is_stats_section:
                stats_buffer.append(line)
                continue
            is_match = ":" in line
            is_context = "-" in line and not is_match
            if not (is_match or is_context):
                continue
            sep = ":" if is_match else "-"
            parts = line.split(sep, 2)
            if len(parts) < 3:
                continue
            h_prefix = Path(parts[0]).name[:10]
            if h_prefix != last_file:
                if current_block and rendered_matches <= limit:
                    yield yield_and_buffer(
                        _render_oob(
                            None,
                            render_template("components/rg_block.html", block=current_block),
                            swap="beforeend:#rg-results",
                        )
                    )
                last_file = h_prefix
                meta = hash_prefix_to_meta.get(h_prefix, {})
                if options.filter_mode == "tagged" and not meta.get("tag"):
                    current_block = None
                    continue
                seen_hashes.add(h_prefix)
                current_block = {
                    "hash": h_prefix,
                    "tag": meta.get("tag"),
                    "title": meta.get("title", ""),
                    "authors": meta.get("authors", ""),
                    "lines": [],
                }
            if current_block:
                if is_match:
                    total_matches += 1
                if rendered_matches < limit:
                    if is_match:
                        rendered_matches += 1
                    formatted_line = html.escape(parts[2].rstrip())
                    if is_match:
                        try:
                            pat = re.compile(f"({re.escape(options.query)})", re.IGNORECASE)
                            formatted_line = pat.sub(r"<mark>\1</mark>", formatted_line)
                        except Exception:
                            pass
                    current_block["lines"].append(
                        {"type": "match" if is_match else "context", "number": parts[1], "text": formatted_line}
                    )

        if current_block and rendered_matches <= limit:
            yield yield_and_buffer(
                _render_oob(
                    None,
                    render_template("components/rg_block.html", block=current_block),
                    swap="beforeend:#rg-results",
                )
            )

        m = re.findall(r"(\d+\.\d+) seconds", "".join(stats_buffer))
        rg_internal_time = f"{float(m[-1]):.3f}s" if m else "0.000s"
        final_stats["matches"] = total_matches
        final_stats["docs"] = len(seen_hashes)

        top_hashes = "|".join([h[:8] for h in list(seen_hashes)[:50]])
        if top_hashes:
            yield yield_and_buffer(_render_export_button("rg", top_hashes))
            final_stats["hashes"] = top_hashes

        stats_html = _render_stats(
            verb="Found",
            matches=total_matches,
            docs=len(seen_hashes),
            noun="files",
            total_time=f"{time.time() - start_time:.3f}s",
            rg_time=rg_internal_time,
        )
        yield yield_and_buffer(_render_oob("rg-stats-header", stats_html))
        set_rg_cache_item(options.search_key, "".join(html_buffer), "html")
        set_rg_cache_item(options.search_key, final_stats, "stats")
    except Exception as e:
        logger.error("RG Details Error: %s", e)
        yield yield_and_buffer(_render_oob(None, f"Ripgrep Error: {html.escape(str(e))}", classes="error"))
