"""
Ripgrep runner and Rich presentation for library full-text extracts.

The runner knows how to:
- build and execute ripgrep commands against the markdown extract tree
- infer presentation mode from raw rg arguments
- map markdown extract paths to mirrored PDF paths

The presenter knows how to:
- render search results from rg --json
- render count results from rg -c / --count
- render file listings from rg --files

The public entry point is RipgrepTools.run_and_present().
"""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import subprocess
from typing import Iterable

from rich.console import Console
from rich.text import Text

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchLine:
    """A single displayed line from ripgrep output."""

    kind: str
    line_number: int | None
    text: str
    submatches: list[tuple[int, int]] = field(default_factory=list)


@dataclass(slots=True)
class FileBlock:
    """A group of search hits or context lines for one file."""

    md_path: Path
    pdf_path: Path
    label: str
    lines: list[SearchLine] = field(default_factory=list)


class RipgrepTools:
    """Run ripgrep and render compact Rich output."""

    def __init__(
        self,
        *,
        console: Console,
        text_dir: Path,
        extractor: str | None,
        md_glob: str = "*.md",
    ) -> None:
        """Initialize the ripgrep helper for a library."""
        self.console = console
        self.text_dir = Path(text_dir)
        self.extractor = extractor
        self.md_glob = md_glob

    # -------------------------------------------------------------------------
    # Top-level orchestration
    # -------------------------------------------------------------------------

    def run_and_present(self, raw_args: list[str]) -> None:
        """Run ripgrep with raw pass-through args and present the output."""
        mode = self.detect_mode(raw_args)
        cmd = self.build_command(raw_args=raw_args, mode=mode)

        proc = self.run_command(cmd)
        if proc is None:
            return

        if proc.stdout is None:
            self.console.print("[red]Failed to read rg output[/red]")
            return

        if mode == "files":
            self.present_files(proc.stdout)
        elif mode == "count":
            self.present_count(proc.stdout)
        else:
            self.present_search(proc.stdout)

    # -------------------------------------------------------------------------
    # Command construction and execution
    # -------------------------------------------------------------------------

    @staticmethod
    def detect_mode(raw_args: list[str]) -> str:
        """Infer presentation mode from the raw ripgrep arguments."""
        if "--files" in raw_args:
            return "files"
        if "-c" in raw_args or "--count" in raw_args or "--count-matches" in raw_args:
            return "count"
        return "search"

    def build_command(self, *, raw_args: list[str], mode: str) -> list[str]:
        """Build the ripgrep command line."""
        cmd = [
            "rg",
            "--encoding",
            "utf-8",
        ]

        # Search mode needs structured output for presentation.
        if mode == "search" and "--json" not in raw_args:
            cmd.append("--json")

        # Restrict to markdown extracts unless the caller explicitly asks otherwise.
        if "--files" not in raw_args and "-g" not in raw_args and "--glob" not in raw_args:
            cmd.extend(["-g", self.md_glob])

        cmd.extend(raw_args)
        cmd.append(str(self.text_dir))

        logger.info("Running ripgrep command: %s", cmd)
        return cmd

    def run_command(self, cmd: list[str]) -> subprocess.Popen | None:
        """Execute the ripgrep subprocess."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            self.console.print("[red]ripgrep (rg) not found on PATH[/red]")
            return None

        return proc

    # -------------------------------------------------------------------------
    # Search-mode presentation
    # -------------------------------------------------------------------------

    def present_search(self, stdout_iter: Iterable[str]) -> None:
        """Render rg --json search output in compact grouped form."""
        blocks = self.parse_search_events(stdout_iter)

        for block_index, block in enumerate(blocks):
            if block_index:
                self.console.print()

            self.console.print(self.render_file_header(block))

            match_index = 0
            for line in block.lines:
                if line.kind == "match":
                    match_index += 1
                    rendered = self.render_match_line(
                        match_index=match_index,
                        line_number=line.line_number,
                        line_text=line.text,
                        submatches=line.submatches,
                    )
                else:
                    rendered = self.render_context_line(
                        line_number=line.line_number,
                        line_text=line.text,
                    )
                self.console.print(rendered, overflow="ignore", crop=False)

    def parse_search_events(self, stdout_iter: Iterable[str]) -> list[FileBlock]:
        """Parse ripgrep JSON search events into per-file blocks."""
        blocks_by_path: dict[Path, FileBlock] = {}
        ordered_paths: list[Path] = []

        for raw_line in stdout_iter:
            raw_line = raw_line.rstrip("\n")
            if not raw_line:
                continue

            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                self.console.print(raw_line, style="dim")
                continue

            event_type = event.get("type")
            if event_type not in {"match", "context"}:
                continue

            data = event["data"]
            path_text = self.extract_text(data.get("path"))
            if path_text is None:
                continue

            md_path = Path(path_text)
            if md_path not in blocks_by_path:
                pdf_path = self.pdf_path_from_md(md_path)
                blocks_by_path[md_path] = FileBlock(
                    md_path=md_path,
                    pdf_path=pdf_path,
                    label=self.short_label(md_path),
                )
                ordered_paths.append(md_path)

            line_text = self.extract_text(data.get("lines")) or ""
            line_number = data.get("line_number")
            submatches = [
                (sub["start"], sub["end"])
                for sub in data.get("submatches", [])
            ]

            blocks_by_path[md_path].lines.append(
                SearchLine(
                    kind=event_type,
                    line_number=line_number,
                    text=line_text.rstrip("\n"),
                    submatches=submatches,
                )
            )

        return [blocks_by_path[path] for path in ordered_paths]

    def render_file_header(self, block: FileBlock) -> Text:
        """Render one clickable file header."""
        text = Text()
        text.append(
            block.label,
            style=f"bold cyan link {block.pdf_path.as_uri()}",
        )
        return text

    def render_match_line(
        self,
        *,
        match_index: int,
        line_number: int | None,
        line_text: str,
        submatches: list[tuple[int, int]],
    ) -> Text:
        """Render one compact match line with highlighted spans."""
        text = Text()
        text.append(f"m{match_index:02d}  ", style="bold cyan")
        text.append(f"{self.format_line_number(line_number)}  ", style="cyan")

        body = Text(line_text, style="white")
        for start, end in submatches:
            body.stylize("bold red", start, end)

        text.append_text(body)
        return text

    def render_context_line(self, *, line_number: int | None, line_text: str) -> Text:
        """Render one compact context line."""
        text = Text()
        text.append("     ", style="dim")
        text.append(f"{self.format_line_number(line_number)}  ", style="dim")
        text.append(line_text, style="dim")
        return text

    # -------------------------------------------------------------------------
    # Count-mode presentation
    # -------------------------------------------------------------------------

    def present_count(self, stdout_iter: Iterable[str]) -> None:
        """Render rg count output as dense aligned columns."""
        rows: list[tuple[int, Path, Path, str]] = []

        for raw_line in stdout_iter:
            raw_line = raw_line.rstrip("\n")
            if not raw_line:
                continue

            count_text, sep, file_text = raw_line.partition(":")
            if not sep:
                self.console.print(raw_line, style="dim")
                continue

            try:
                count = int(count_text.strip())
            except ValueError:
                self.console.print(raw_line, style="dim")
                continue

            md_path = Path(file_text.strip())
            pdf_path = self.pdf_path_from_md(md_path)
            rows.append((count, md_path, pdf_path, self.short_label(md_path)))

        if not rows:
            return

        width = max(len(str(count)) for count, *_ in rows)

        for count, _, pdf_path, label in rows:
            text = Text()
            text.append(f"{count:>{width}d}  ", style="bold cyan")
            text.append(label, style=f"cyan link {pdf_path.as_uri()}")
            self.console.print(text, overflow="ignore", crop=False)

    # -------------------------------------------------------------------------
    # Files-mode presentation
    # -------------------------------------------------------------------------

    def present_files(self, stdout_iter: Iterable[str]) -> None:
        """Render rg --files output as clickable compact labels."""
        for raw_line in stdout_iter:
            raw_line = raw_line.rstrip("\n")
            if not raw_line:
                continue

            md_path = Path(raw_line)
            pdf_path = self.pdf_path_from_md(md_path)
            label = self.short_label(md_path)

            text = Text()
            text.append(label, style=f"cyan link {pdf_path.as_uri()}")
            self.console.print(text, overflow="ignore", crop=False)

    # -------------------------------------------------------------------------
    # Path and filename helpers
    # -------------------------------------------------------------------------

    def pdf_path_from_md(self, md_path: Path) -> Path:
        """Map a markdown extract path to the mirrored PDF path."""
        parts = list(md_path.parts)
        try:
            index = parts.index("ShardedFullText")
            parts[index] = "ShardedDocLibrary"
        except ValueError:
            pass

        pdf_path = Path(*parts)

        suffix = self.extractor_suffix()
        if suffix and pdf_path.name.endswith(suffix):
            pdf_name = pdf_path.name.removesuffix(suffix) + ".pdf"
            pdf_path = pdf_path.with_name(pdf_name)
        else:
            pdf_path = pdf_path.with_suffix(".pdf")

        return pdf_path

    def short_label(self, md_path: Path) -> str:
        """Convert a controlled filename to a compact bibliographic label."""
        name = md_path.name
        suffix = self.extractor_suffix()
        if suffix and name.endswith(suffix):
            name = name.removesuffix(suffix)
        else:
            name = md_path.stem

        # First try the common "Author - Year - Title" pattern.
        parts = [part.strip() for part in name.split(" - ") if part.strip()]
        if len(parts) >= 3:
            author = parts[0]
            year = parts[1]
            title = " - ".join(parts[2:])
            return f"{author} ({year}) {title}"

        # Then try the common "Author_Year_Title" pattern.
        parts = [part.strip() for part in name.split("_") if part.strip()]
        if len(parts) >= 3 and parts[1].isdigit():
            author = parts[0]
            year = parts[1]
            title = " ".join(parts[2:])
            return f"{author} ({year}) {title}"

        # Fallback: keep the cleaned stem.
        return name

    def extractor_suffix(self) -> str | None:
        """Return the controlled markdown suffix for the configured extractor."""
        if self.extractor is None:
            return None
        return f".{self.extractor}.md"

    # -------------------------------------------------------------------------
    # Small utilities
    # -------------------------------------------------------------------------

    @staticmethod
    def extract_text(node: object) -> str | None:
        """Extract rg JSON text fields from path/lines objects."""
        if isinstance(node, dict) and "text" in node:
            return node["text"]
        return None

    @staticmethod
    def format_line_number(line_number: int | None) -> str:
        """Format a line number in a fixed compact width."""
        if line_number is None:
            return " " * 5
        return f"{line_number:05d}"
