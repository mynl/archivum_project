"""Various utilities for archivum."""

from collections import defaultdict
from functools import partial
from pathlib import Path
import re
import unicodedata
from IPython.display import display as ip_display
import numpy as np
import pandas as pd

from greater_tables import GT


def safe_int(s):
    """
    Safe format of s as a year for greater_tables.

    By default s may be interpreted as a float so str(x) give 2015.0
    which is not wanted. Hence this function is needed.
    """
    try:
        return f"{int(s)}"
    except ValueError:
        if s == "":
            return ""
        else:
            return s


def safe_file_size(s):
    """
    Safe format of s as a year for greater_tables.

    By default s may be interpreted as a float so str(x) give 2015.0
    which is not wanted. Hence this function is needed.
    """
    try:
        sz = int(s)
        if sz < 1 << 10:
            return f"{sz:,d}B"
        elif sz < 1 << 18:
            return f"{sz >> 10:,d}KB"
        elif sz < 1 << 28:
            return f"{sz >> 20:,d}MB"
        elif sz < 1 << 38:
            return f"{sz >> 30:,d}GB"
        elif sz < 1 << 48:
            return f"{sz >> 40:,d}TB"
        else:
            return f"{sz >> 50:,d}PB"
    except ValueError:
        if s == "":
            return ""
        else:
            return s


# make the library display function
def make_qd(max_string_length=50, max_rows=10, display_func=None, **gt_kwargs):
    """
    Make a qd function with sensible defaults.

    If display_func is None use IPython.display display.
    """

    def default_formatter(x):
        """
        For raw columns.

        The issue is that cols with ints and '' strings are not recognized as int by GT.
        """
        if isinstance(x, int):
            return f"{x:,d}"
        elif isinstance(x, float):
            return f"{x:,.2f}"
        else:
            return str(x)[:max_string_length]

    default_args = {
        "large_ok": True,
        "show_index": False,
        "formatters": {
            "size": safe_file_size,
        },
        "raw_cols": ["year", "index", "node", "links", "number"],
        "aligners": {
            "year": "r",
            "index": "l",
            "node": "r",
            "links": "r",
            "number": "r",
        },
    }
    if max_string_length > 0:
        default_args["default_formatter"] = default_formatter

    default_args = default_args | gt_kwargs

    fGT = partial(GT, **default_args)
    display_func = display_func or ip_display
    caption_str = f"{{caption}} (Truncation: {max_rows} rows/{max_string_length} cols)"

    def qd(df, **kwargs):
        """Generic display function."""
        caption = kwargs.get("caption", None)
        if caption:
            kwargs["caption"] = caption_str.format(caption=caption)
        if isinstance(df, list):
            df = df[:max_rows]
        else:
            df = df.head(max_rows)
        display_func(fGT(df, **kwargs))

    return qd


def remove_accents(s: str) -> str:
    """Remove accents from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def accent_mapper_dict(names, verbose=False):
    """Make dict mapper for name -> accented name from list of names."""
    # both versions of the name must be in names
    # not 100% reliable!
    canonical = defaultdict(set)

    for name in names:
        key = remove_accents(name)
        canonical[key].add(name)
    if verbose:
        mapper = {k: sorted(v) for k, v in canonical.items() if len(v) > 1}
    else:
        mapper = {k: sorted(v)[-1] for k, v in canonical.items() if len(v) > 1}
    return mapper


def suggest_filename(s):
    """Clean file name for windows."""
    pass


class TagAllocator:
    def __init__(self, existing: set[str]):
        """Class to determine the next key (@AuthorYYYY) given a list of existing keys."""
        self.existing = set(existing)
        self.pattern = re.compile(r"^(.+?)(\d{4})?([a-z]?)$")
        self.allocators = defaultdict(self._make_iter)

    def _make_iter(self):
        def gen():
            yield ""  # first without suffix
            for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
                yield c

        return gen()

    def next_tag(self, tag) -> str:
        """Return the next available tag matching the input tag = NameYYYY format."""
        # name: str, year: int
        m = self.pattern.match(tag)
        try:
            name = m[1]
            year = m[2]
            if year is None:
                year = "YYYY"
        except TypeError:
            # m - none, no match found
            print(f"Type Error for {tag = }")
            return tag
        else:
            return self.get_tag(name, year)

    __call__ = next_tag

    def get_tag(self, name: str, year: str) -> str:
        """Create a tag for given name and year."""
        base = f"{name}{year}"
        it = self.allocators[(name, str(year))]
        while True:
            suffix = next(it)
            candidate = base + suffix
            if candidate not in self.existing:
                self.existing.add(candidate)
                return candidate


def sanitize_windows_component(name: str, max_length: int = 255) -> str:
    """
    Sanitize a string so it can be safely used as a single Windows
    path component (file or directory name).

    Rules:
      - Remove control characters and invalid punctuation.
      - Strip leading/trailing spaces and dots.
      - Avoid reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
      - Enforce a per-component length cap.
    """
    # Remove NUL and other control chars, plus Windows-invalid characters.
    # Invalid: < > : " / \ | ? *
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)

    # Strip leading/trailing spaces and dots.
    name = name.strip(" .")

    # Collapse multiple underscores.
    name = re.sub(r"_+", "_", name)

    # Avoid empty component.
    if not name:
        name = "unnamed"

    # Avoid reserved device names (extension is ignored).
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    root = name.split(".", 1)[0]
    if root.upper() in reserved:
        name = name + "_"

    # Enforce max_length per component.
    if len(name) > max_length:
        # Try to preserve the extension if present.
        if "." in name:
            stem, ext = name.rsplit(".", 1)
            keep_stem = max_length - 1 - len(ext)
            if keep_stem <= 0:
                # Extension too long; fall back to truncating everything.
                name = name[:max_length]
            else:
                name = stem[:keep_stem] + "." + ext
        else:
            name = name[:max_length]

    return name


def suggest_name(author: str, title: str, year: str | int):
    """
    Figure standard dir name and file name from author, title, and year.

    Names are make filename safe

    author, str, in standard last, first [and...] format
    title, st
    year, str

    """
    year = str(year).strip()
    title = title.strip()
    author = author.strip()

    # directory from author name(s)
    split_author = author.split(" and ") if author else []
    dir_name = ", ".join([i.split(",")[0] for i in split_author][:3])
    if len(split_author) > 3:
        dir_name = f"{dir_name} et al" if dir_name else "et al"

    file_name = f"{year}_{title}"

    dir_name = sanitize_windows_component(dir_name)
    file_name = sanitize_windows_component(file_name)

    return dir_name, file_name


def rename(original_file_path: Path, pdf_dir_path: Path, dir_name: str, file_name: str):
    """Hard link original file into pdf_dir/dir_name/file_name."""

    new_name = Path(file_name).with_suffix(original_file_path.suffix)
    parent_dir = pdf_dir_path / dir_name
    parent_dir.mkdir(parents=True, exist_ok=True)
    new_path = parent_dir / new_name
    if new_path.exists():
        logger.warning("new path exists! Unlinking...")
        # new_path.unlink()

    # make new link
    logger.info("%s --> %s", new_path, self.doc_path)
    print(f"{new_path} ==> {self.doc_path}")

    # save it
    self._new_doc_path = new_path
    # new_path.hardlink_to(self.doc_path)
