from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from .bibtex import dict_to_bibtex

DEFAULT_CSL = '/s/TELOS/Biblio/journal-of-risk-and-uncertainty.csl'

@dataclass(slots=True)
class QmdParser:
    """
    Parse a Quarto .qmd file (UTF-8), expanding nested {{< include ... >}} clauses,
    then splitting into:
      - all_text: expanded full text (includes everything)
      - header: YAML front matter content (between --- lines), or "" if absent
      - text_paras: paragraphs excluding code blocks and HTML comments, keeping
        ::: ... ::: div blocks together as one paragraph
      - code_blocks: list of fenced ``` ... ``` blocks (verbatim)
      - comments: list of <!-- ... --> HTML comments (verbatim)

    Usage:
        qp = QmdParser("path/to/file.qmd")
        qp.text_paras
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
        """Read, expand includes, and populate all parsed parts."""
        self.path = Path(self.path)
        self.all_text = self._expand_includes(self.path, seen=set())

        # 1) Split out code blocks first.
        no_code = self._strip_code_blocks(self.all_text)

        # 2) Extract comments next.
        no_code_no_comments = self._strip_comments(no_code)

        # 3) Extract YAML front matter (from start, after include expansion).
        body = self._extract_yaml_header(no_code_no_comments)

        # 4) Paragraph splitting (keep ::: blocks together).
        self.text_paras = self._split_paragraphs_keep_divs(body)

    def to_dict(self) -> dict[str, object]:
        """Return the parsed components as a dict (compatible with your earlier shape)."""
        return {
            "all_text": self.all_text,
            "header": self.header,
            "text_paras": self.text_paras,
            "code_blocks": self.code_blocks,
            "comments": self.comments,
        }

    def _expand_includes(self, path: Path, seen: set[Path]) -> str:
        """Recursively expand {{< include ... >}} clauses, resolving paths relative to each file."""
        path = path.resolve()

        # Cycle protection.
        if path in seen:
            return f"\n<!-- include cycle detected: {path.as_posix()} -->\n"

        if not path.exists():
            return f"\n<!-- include missing: {path.as_posix()} -->\n"

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
        """Extract fenced code blocks and replace them with a newline."""
        self.code_blocks.clear()

        def repl(m: re.Match[str]) -> str:
            self.code_blocks.append(m.group(0))
            return "\n"

        return self._code_fence_re.sub(repl, text)

    def _strip_comments(self, text: str) -> str:
        """Extract HTML comments and replace them with a newline."""
        self.comments.clear()

        def repl(m: re.Match[str]) -> str:
            self.comments.append(m.group(0))
            return "\n"

        return self._html_comment_re.sub(repl, text)

    def _extract_yaml_header(self, text: str) -> str:
        """Extract YAML front matter from the start (if present) and return the remaining body."""
        self.header = ""
        m = self._yaml_frontmatter_re.match(text)
        if not m:
            return text
        self.header = m.group(1)
        return text[m.end():]

    @staticmethod
    def _split_paragraphs_keep_divs(text: str) -> list[str]:
        """
        Split text into paragraphs by blank lines, but treat ::: ... ::: blocks
        as a single paragraph, even if they contain blank lines.
        """
        lines = text.splitlines()

        paras: list[str] = []
        buf: list[str] = []

        def flush_buf() -> None:
            if not buf:
                return
            para = "\n".join(buf).strip()
            if para:
                paras.append(para)
            buf.clear()

        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]

            # Start of a div block.
            if line.lstrip().startswith(":::"):
                flush_buf()
                div_lines = [line]
                i += 1

                # Collect until the next ::: line (inclusive). If not found, take to EOF.
                while i < n:
                    div_lines.append(lines[i])
                    if lines[i].lstrip().startswith(":::"):
                        i += 1
                        break
                    i += 1

                div_para = "\n".join(div_lines).strip()
                if div_para:
                    paras.append(div_para)
                continue

            # Blank line: paragraph boundary.
            if line.strip() == "":
                flush_buf()
                i += 1
                continue

            buf.append(line)
            i += 1

        flush_buf()
        return paras

    def citations(self) -> list[str]:
        """Return a list of @bibtex-style citation keys in appearance order (duplicates kept)."""
        cite_rex = re.compile(r"(?<!@)@(?!REF)([A-Z][A-Za-z0-9]+)")
        return sorted(set([m.group(1) for m in cite_rex.finditer(self.all_text)]))

    def citations_ex(self) -> dict[int, list[str]]:
        """Return {para_index: [citation_key, ...]} for paragraphs containing citations."""
        cite_rex = re.compile(r"(?<!@)@(?!REF\b)([A-Z][A-Za-z0-9]+)")

        out: dict[int, list[str]] = {}

        for i, para in enumerate(self.text_paras):
            hits = [m.group(1) for m in cite_rex.finditer(para)]
            if hits:
                out[i] = hits

        return out

    def ref_summary(self, out_path: Path, lib: object, *,
                    csl_value: str = "", execute: bool = False,
                    abstract: bool = True) -> list[str]:
        """
        Create a reference-summary directory containing:
          1) a .qmd file (same basename as self.path) with a bullet list of cited tags
          2) symlinks to each cited item's file path from lib.database

        Safety:
          - creates out_path if needed
          - refuses if out_path is (or is inside) self.path.parent (i.e., could clobber source area)
          - refuses if out_path is a parent of self.path (explicitly requested)
          - when execute=True: clears out_path first (subject to safety/structure checks)

        lib.database must be a DataFrame with columns: tag, title, author, year, path
        lib must have .abspath method to convert path to absolute path.
        """
        src = self.path.resolve()
        out_path = Path(out_path).resolve()

        actions: list[str] = []

        # Safety: out_path must not be a parent of the source file.
        if out_path in src.parents:
            raise ValueError(f"ref_summary safety: out_path is a parent of source file: {out_path}")

        # Extra safety: do not allow writing into the source directory tree.
        if out_path == src.parent or out_path in src.parent.parents or src.parent in out_path.parents:
            # The condition "src.parent in out_path.parents" means out_path is inside source dir tree.
            # The condition "out_path in src.parent.parents" means out_path is above source dir tree.
            # The only safe region is "elsewhere".
            if src.parent in out_path.parents:
                raise ValueError(f"ref_summary safety: out_path is inside source directory tree: {out_path}")
            if out_path == src.parent:
                raise ValueError(f"ref_summary safety: out_path equals source directory: {out_path}")

        # Ensure directory exists (even in dry-run, so path checks are meaningful).
        if not out_path.exists():
            actions.append(f"mkdir {out_path}")
            if execute:
                out_path.mkdir(parents=True, exist_ok=True)

        # Pre-check structure: allow at most one .qmd in out_path; other entries should be symlinks
        # or common build artifacts (pdf/html/tex/etc.).
        self._check_dir_structure(out_path)

        if execute:
            # Clear out_path contents (after structure check).
            actions.extend(self._clear_dir(out_path, execute=True))
        else:
            actions.extend(self._clear_dir(out_path, execute=False))

        # Build tag -> para indices mapping from text_paras.
        cite_rex = re.compile(r"(?<!@)@(?!REF\b)([A-Z][A-Za-z0-9]+)")
        tag_to_paras: dict[str, list[int]] = {}

        for i, para in enumerate(self.text_paras):
            hits = [m.group(1) for m in cite_rex.finditer(para)]
            for tag in hits:
                tag_to_paras.setdefault(tag, []).append(i)

        # sort by tag
        tags_sorted = sorted(set(tag_to_paras))

        # sort by first mention paragraph
        items_sorted = sorted(tag_to_paras.items(), key=lambda kv: min(kv[1]))
        tags_sorted  = dict(items_sorted)
        # save
        self.tags_sorted.extend(tags_sorted)

        # Figure and bibtex (FILE)
        bibtex_value = Path(lib.config.bibtex_file).as_posix()
        if not csl_value:
            csl_value = DEFAULT_CSL

        # Write summary.qmd using same name as the source file (but never overwriting the source).
        out_qmd = (out_path / src.name).resolve()
        if out_qmd == src:
            raise ValueError(f"ref_summary safety: output .qmd would overwrite source: {out_qmd}")

        # Compose qmd content.
        qmd_text = self._build_ref_summary_qmd(
            csl_value=csl_value,
            bibtex_value=bibtex_value,
            tags_sorted=tags_sorted,
            tag_to_paras=tag_to_paras,
            abstract=abstract,
            lib=lib,
        )

        actions.append(f"write {out_qmd}")
        if execute:
            out_qmd.write_text(qmd_text, encoding="utf-8")

        # Create symlinks for each cited reference.
        df = lib.database  # expected DataFrame
        for tag in tags_sorted:
            row = df.loc[df["tag"] == tag]
            if row.empty:
                actions.append(f"missing in lib.database: {tag}")
                continue

            # If multiple, take the first deterministically.
            r0 = row.iloc[0]
            # make path absolute via Library function
            src_path = lib.abspath(r0["path"])

            if not src_path.exists():
                actions.append(f"missing file for {tag}: {src_path}")
                continue

            link_name = src_path.name
            link_path = (out_path / link_name)

            actions.append(f"symlink {link_path} -> {src_path}")
            if execute:
                # If something already exists at link_path (should not after clear), remove it.
                if link_path.exists() or link_path.is_symlink():
                    link_path.unlink()
                link_path.symlink_to(src_path)

        return actions


    def _check_dir_structure(self, out_path: Path) -> None:
        """
        Ensure out_path contains at most one .qmd file; other entries must be symlinks
        or allowed build artifacts (pdf/html/tex/etc.). Refuse on unexpected directories/files.
        """
        if not out_path.exists():
            return

        qmds = list(out_path.glob("*.qmd"))
        if len(qmds) > 1:
            raise ValueError(f"ref_summary safety: out_path has >1 .qmd files: {[p.name for p in qmds]}")

        allowed_suffixes = {
            ".pdf",
            ".html",
            ".htm",
            ".tex",
            ".log",
            ".aux",
            ".toc",
            ".out",
            ".synctex",
            ".synctex.gz",
            ".json",
            ".xml",
            ".png",
            ".jpg",
            ".jpeg",
            ".svg",
            ".css",
            ".js",
        }

        for p in out_path.iterdir():
            if p.is_dir():
                if p.name.endswith('_files'):
                    continue
                raise ValueError(f"ref_summary safety: unexpected directory in out_path: {p.name}")

            if p.suffix.lower() == ".qmd":
                continue

            if p.is_symlink():
                continue

            if p.suffix.lower() in allowed_suffixes:
                continue

            raise ValueError(f"ref_summary safety: unexpected file in out_path: {p.name}")

    def _clear_dir(self, out_path: Path, execute: bool) -> list[str]:
        """Remove everything in out_path (files and symlinks). Assumes _check_dir_structure already passed."""
        actions: list[str] = []
        if not out_path.exists():
            return actions

        for p in out_path.iterdir():
            actions.append(f"rm {p}")
            if execute:
                try:
                    p.unlink()
                except PermissionError:
                    pass
        return actions

    def _build_ref_summary_qmd(
        self,
        csl_value: str,
        bibtex_value: str,
        tags_sorted: list[str],
        tag_to_paras: dict[str, list[int]],
        abstract: bool,
        lib: object,
    ) -> str:
        """Build the summary .qmd content."""
        lines: list[str] = []

        # YAML header (requested: build html and tex, use tectonic engine; include csl and bibtex).
        lines.append("---")
        lines.append("title: Reference summary")
        lines.append("author: archivum.ref-summary")
        if csl_value:
            lines.append(f"csl: {csl_value}")
        if bibtex_value:
            lines.append(f"bibliography: {bibtex_value}")
        lines.append("date-modified: last-modified")
        lines.append("format:")
        lines.append("  html:")
        # lines.append("    theme: cosmo")
        lines.append("    theme: litera")
        lines.append("    smooth-scroll: true")
        lines.append("    citations-hover: true")
        lines.append("    page-layout: article")
        lines.append("    link-external-icon: true")
        lines.append("    link-external-newwindow: true")

        lines.append("  pdf:")
        lines.append("    documentclass: article")
        lines.append("    papersize: a4")
        lines.append("    fontsize: 10pt")
        lines.append("    keep-tex: true")
        lines.append("    geometry: margin=1in")
        lines.append("    reference-section-title: 'References'")
        lines.append("    pdf-engine: tectonic")
        lines.append("---")
        lines.append("")
        lines.append("## References")
        lines.append("")

        df = lib.database  # expected DataFrame

        for tag in tags_sorted:
            row = df.loc[df["tag"] == tag]
            title = ""
            author = ""
            year = ""

            if not row.empty:
                r0 = row.iloc[0]
                title = "" if r0.get("title") is None else str(r0.get("title"))
                author = "" if r0.get("author") is None else str(r0.get("author"))
                year = "" if r0.get("year") is None else str(r0.get("year"))

            paras = tag_to_paras.get(tag, [])
            paras_str = ", ".join(str(i) for i in paras)

            # The user's example shows *title*; you prefer no italics generally,
            # but the user explicitly asked for *title* here, so we follow that.
            # If you want, change *...* to plain text.
            if title:
                if title.startswith("{"): title = title[1:]
                if title.endswith("}"): title = title[:-1]
                title_part = f"*{title}*"
            else:
                title_part = ""

            meta_parts = []
            if author:
                meta_parts.append(author)

            if year:
                meta_parts.append(year)

            meta = ", ".join(meta_parts).strip()
            if meta:
                meta = f", {meta}"

            doc = lib.abspath(r0.path)
            doc_link = f'<a target="_blank" href="{doc}">file</a>.'
            lines.append(f"* {tag} [@{tag}], {title_part}{meta}, (paras: {paras_str}), {doc_link}")
            if abstract:
                text_file = lib.textpath(r0.path)
                txt = ""
                if r0['type'] != "book" and text_file.exists():
                    try:
                        txt = text_file.read_text(encoding='utf-8')
                        txt = self._quick_abstract(txt)
                    except FileNotFoundError:
                        pass
                        print(f'text file {text_file} not found?')
                if txt:
                    lines.append(f"  > {txt}")
                # line break to space out bullets
            lines.append("")
        lines.append("")
        return "\n".join(lines)

    def _quick_abstract(self, text):
        """Try and find the abstract."""
        st = text.lower().find('abstract') + len('abstract')
        ans = []
        # 8 = len(abstract)
        for i in text[st:(st+4008)].split('\n'):
            if not i: continue
            if len(i) > 20:
                ans.append(i)
            else:
                # subsequent short line
                break
        out =  ' '.join(ans)
        out = re.sub(r'Further reproduction prohibited without permission\. ?|Reproduced with permission of the copyright owner\. ?', '', out)
        return out

    def generate_bibtex(self, lib: object, out_file: Path) -> int:
        """
        Extract all @cite keys from the QMD, match them in lib.ref_df,
        and write a new .bib file containing only those entries.
        Returns the number of entries written.
        """
        tags = self.citations()
        if not tags:
            return 0

        # Match tags in library
        # lib.ref_df is expected to be a pandas DataFrame
        df = lib.ref_df
        matches = df[df['tag'].isin(tags)]

        if matches.empty:
            return 0

        bib_entries = []
        # Sort by tag to be nice
        for _, row in matches.sort_values("tag").iterrows():
            bib_entries.append(dict_to_bibtex(row))

        bib_content = "\n\n".join(bib_entries)
        out_file.write_text(bib_content, encoding="utf-8")
        return len(matches)
