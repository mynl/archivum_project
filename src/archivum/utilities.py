"""Various utilities for archivum."""

from collections import defaultdict
from functools import partial
import logging
import os
from pathlib import Path
import shutil
import re
import stat
import unicodedata
from IPython.display import display as ip_display
import numpy as np
import pandas as pd

import subprocess
from tqdm import tqdm

from greater_tables import GT


logger = logging.getLogger(__name__)


def assign_ref_doc_priority(ref_doc: pd.DataFrame) -> pd.DataFrame:
    """
    Give every ref-doc row a contiguous per-tag ``priority``, 0 = primary.

    ``priority`` orders the documents attached to one tag: 0 is the one that
    opens, 1, 2, ... are alternates in preference order. Promote a document by
    zeroing its priority and demoting the incumbent (see
    ``Library.replace_document``).

    This is deliberately NOT ``version``. ``version`` identifies *which sharded
    copy* of a content hash a row points at -- one physical file linked from
    several references is sharded under several canonical names -- and
    ``ref-doc`` joins ``doc`` on ``(hash, version)``. Reusing it for ordering
    would repoint rows at the wrong file.

    Existing values are respected: rows are only re-ranked to close gaps and to
    put rows that carry no value yet at the end of their tag. Libraries written
    before the column existed get ranked by current row order, which is what
    the old ``.iloc[0]`` callers already resolved to.
    """
    if ref_doc.empty:
        if "priority" in ref_doc.columns:
            return ref_doc
        out = ref_doc.copy()
        out["priority"] = pd.Series(dtype="int64")
        return out

    out = ref_doc.copy()
    existing = (
        pd.to_numeric(out["priority"], errors="coerce")
        if "priority" in out.columns
        else pd.Series(np.nan, index=out.index, dtype="float64")
    )
    # within a tag: ranked rows first (by rank), then unranked, each group
    # keeping its current relative order
    out["_unranked"] = existing.isna().to_numpy()
    out["_rank"] = existing.fillna(0).to_numpy()
    out["_row"] = np.arange(len(out))
    out = out.sort_values(["tag", "_unranked", "_rank", "_row"], kind="stable")
    out["priority"] = out.groupby("tag").cumcount().astype("int64")
    # restore the original row order so the feather stays diff-stable
    out = out.sort_values("_row", kind="stable").drop(
        columns=["_unranked", "_rank", "_row"]
    )
    return out.reset_index(drop=True)


def djvu_convert_file(in_path: Path, out_path: Path, verbose: bool = False, config=None) -> bool:
    """
    Convert a DjVu file to a searchable PDF.
    
    Uses ddjvu for initial conversion and ocrmypdf (via WSL) for OCR.
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    
    if not in_path.exists():
        logger.error(f"Input file not found: {in_path}")
        return False

    temp_pdf = out_path.with_suffix(".temp.pdf")
    
    try:
        # Get ddjvu command from config or default
        ddjvu_exe = "ddjvu"
        if config and hasattr(config, 'ddjvu_command'):
            ddjvu_exe = config.ddjvu_command

        # 1. Convert DjVu to PDF using ddjvu
        ddjvu_cmd = [ddjvu_exe, "-format=pdf", str(in_path), str(temp_pdf)]
        if verbose:
            print(f"Running: {' '.join(ddjvu_cmd)}")
        
        subprocess.run(ddjvu_cmd, check=True, capture_output=not verbose)
        
        # 2. Add OCR layer using ocrmypdf via WSL
        # We need to translate Windows paths to WSL paths
        def to_wsl_path(p: Path) -> str:
            # Use as_posix() to get forward slashes, which wslpath handles safely
            # without double-escaping issues.
            windows_path = str(p.absolute().as_posix())
            res = subprocess.run(["wsl", "wslpath", "-a", windows_path], 
                               capture_output=True, text=True, check=True)
            return res.stdout.strip()

        wsl_temp_pdf = to_wsl_path(temp_pdf)
        wsl_out_path = to_wsl_path(out_path)

        ocrmy_cmd = ["wsl", "ocrmypdf", "--skip-text", wsl_temp_pdf, wsl_out_path]
        if verbose:
            print(f"Running: {' '.join(ocrmy_cmd)}")
            
        subprocess.run(ocrmy_cmd, check=True, capture_output=not verbose)
        
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during conversion of {in_path}: {e}")
        if e.stderr:
            err_msg = e.stderr if isinstance(e.stderr, str) else e.stderr.decode(errors="replace")
            logger.error(f"Subprocess stderr: {err_msg}")
        return False
    except FileNotFoundError as e:
        logger.error(f"Required tool not found: {e}")
        return False
    finally:
        # Cleanup temp file
        if temp_pdf.exists():
            try:
                temp_pdf.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete temp file {temp_pdf}: {e}")


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

    if max_rows > 0:
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
    else:
        def qd(df, **kwargs):
            """Generic display function."""
            caption = kwargs.get("caption", None)
            if caption:
                kwargs["caption"] = caption_str.format(caption=caption)
            display_func(fGT(df, **kwargs))

    return qd


def remove_accents(s: str) -> str:
    """Remove accents from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def trim_author(s):
    """Clean author string: short names, truncate at 3 with et al. if more."""
    if not isinstance(s, str) or not s:
        return ""
    # Split and clean
    # Remove {} and handle LaTeX-style author strings
    s = s.replace('{', '').replace('}', '')
    author_bits = [i.split(",")[0].strip() for i in s.split(" and ")]
    if len(author_bits) > 3:
        name = ", ".join(author_bits[:3]) + " et al."
    elif len(author_bits) == 3:
        name = ", ".join(author_bits[:2]) + f", and {author_bits[2]}"
    elif len(author_bits) == 2:
        name = f"{author_bits[0]} and {author_bits[1]}"
    else:
        name = author_bits[0] if author_bits else ""
    return name


def clean_latex(s):
    """Remove LaTeX braces from a string."""
    if not isinstance(s, str):
        return ""
    return s.replace('{', '').replace('}', '')


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
    # titles often enclosed in braces
    title = re.sub(r"^\{(.*)\}$", r"\1", title).strip()
    # get rid of {} within names too (this happens!)
    author = re.sub(r"{|}", "", author).strip()

    # directory from author name(s)
    split_author = author.split(" and ") if author else []
    dir_name = ", ".join([i.split(",")[0] for i in split_author][:3])
    if len(split_author) > 3:
        dir_name = f"{dir_name}, et al"

    file_name = f"{year}_{title}"

    dir_name = sanitize_windows_component(dir_name)
    file_name = sanitize_windows_component(file_name)

    return dir_name, file_name


def rename(
    original_doc_name: str,
    doc_hash: str,
    pdf_dir_path: Path,
    dir_name: str,
    file_name: str,
    hash_len: int = 6,
    execute: bool = False,
) -> bool:
    """
    Hard link original file into pdf_dir/dir_name/file_name.

    Returns True if copied, else false
    """
    original_doc_path = Path(original_doc_name)
    new_name = Path(f"{file_name}-{doc_hash[:hash_len]}").with_suffix(
        original_doc_path.suffix
    )
    parent_dir = pdf_dir_path / dir_name
    parent_dir.mkdir(parents=True, exist_ok=True)
    new_path = parent_dir / new_name
    if new_path.exists():
        # almost certainly the same underlying file...
        logger.info("guessing the same file and skipping %s", new_path)
        return False

        # the unlink route
        logger.info("new path exists, unlinking %s", new_path)
        # --- Fix: Ensure write permission before unlinking on Windows ---
        try:
            # Check current permissions
            file_mode = os.stat(new_path).st_mode
            # If the read-only bit (S_IREAD) is set
            if not file_mode & stat.S_IWRITE:
                # Set write permission (S_IWRITE) for the owner
                os.chmod(new_path, stat.S_IWRITE)
                logger.info("Removed read-only attribute from %s", new_path)
        except Exception as e:
            logger.warning("Could not change file attributes for %s: %s", new_path, e)
        # -------------------------------------------------------------
        if execute:
            new_path.unlink()

    # copy into place (not a hardlink: the store may be on another volume)
    logger.info("new: %s --> old: %s", new_path, original_doc_path)
    if execute:
        shutil.copy2(original_doc_path, new_path)
    return True

