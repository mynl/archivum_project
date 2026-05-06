from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import pandas as pd
import logging

from .bibtex import dict_to_bibtex

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
        tag_link = f'**<a target="_blank" href="/view/{tag}">{tag}</a>**'
    else:
        doc_path = None
        if hasattr(row, "path") and pd.notna(row.path):
            doc_path = lib.abspath(row.path)
        
        if doc_path and doc_path.exists():
            tag_link = f'**<a target="_blank" href="{doc_path}">{tag}</a>**'
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
            pdf = pdf.merge(extra_info[['tag', 'path', 'hash']], on="tag", how="left")
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
