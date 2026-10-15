"""
Versions of archivum and of the packages and tools it runs on.

Collected for the web About page, but Flask-free so the CLI and tests can use
it. External tools are probed with a subprocess, once per process: the answers
cannot change while the server runs, and the first probe of a slow tool
(quarto starts a runtime) should not be paid again on every page load.
"""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError, version
import platform
import re
import shutil
import subprocess
from pathlib import Path

from . import __version__

# Distribution names, as importlib.metadata knows them.
PYTHON_PACKAGES = (
    "flask",
    "waitress",
    "pandas",
    "numpy",
    "pyarrow",
    "querexfuzz",
    "skimmatch",
    "uber-shell",
    "pymupdf",
    "pypdf",
    "pybtex",
    "sentence-transformers",
    "torch",
    "umap-learn",
    "hdbscan",
    "networkx",
    "matplotlib",
    "watchdog",
)

# Label -> arguments that make the tool print its version. ddjvu has no
# version flag; --help at least proves it runs.
EXTERNAL_TOOLS = {
    "rg": ["--version"],
    "pandoc": ["--version"],
    "quarto": ["--version"],
    "tectonic": ["--version"],
    "pdftotext": ["-v"],
}

TOOL_TIMEOUT_SECONDS = 5

NOT_FOUND = "not found"
NOT_INSTALLED = "not installed"

# src/archivum/versions.py -> the checkout root, when there is one
REPO_ROOT = Path(__file__).resolve().parents[2]


def package_versions(names=PYTHON_PACKAGES) -> dict[str, str]:
    """
    Installed version of each Python distribution.

    Parameters
    ----------
    names : iterable of str
        Distribution names.

    Returns
    -------
    dict
        Name to version, or ``"not installed"``.
    """
    out = {}
    for name in names:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = NOT_INSTALLED
    return out


@cache
def tool_version(executable: str, args: tuple[str, ...] = ("--version",)) -> str:
    """
    First non-empty output line of ``executable args``, or ``"not found"``.

    Parameters
    ----------
    executable : str
        A command on ``PATH`` or a path to one.
    args : tuple of str
        Arguments that make the tool report its version.

    Returns
    -------
    str
        The line, ``"not found"`` when the executable is absent or will not
        run, or ``"found (no version reported)"`` when it runs but its first
        line carries no version number.

    Notes
    -----
    stdout is read first, then stderr: pdftotext prints its version to
    stderr. The exit code is ignored, since some tools exit non-zero after
    printing usage. Never raises.
    """
    resolved = shutil.which(executable)
    if not resolved:
        return NOT_FOUND
    try:
        proc = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return NOT_FOUND
    for stream in (proc.stdout, proc.stderr):
        for line in (stream or "").splitlines():
            if line.strip():
                line = line.strip()
                return line if re.search(r"\d", line) else "found (no version reported)"
    return "found (no version reported)"


def tool_versions(ddjvu_command: str = "ddjvu") -> dict[str, str]:
    """
    Versions of the external tools archivum shells out to.

    Parameters
    ----------
    ddjvu_command : str
        The configured ``ddjvu_command``, which may be a full path.

    Returns
    -------
    dict
        Tool label to :func:`tool_version` result.
    """
    out = {label: tool_version(label, tuple(args)) for label, args in EXTERNAL_TOOLS.items()}
    out["ddjvu"] = tool_version(ddjvu_command or "ddjvu", ("--help",))
    return out


@cache
def git_revision(repo_root: Path = REPO_ROOT) -> str:
    """
    Short commit hash of a git checkout, with ``+dirty`` for tracked changes.

    Returns ``""`` when archivum is not running from a checkout or git is
    unavailable.
    """
    if not (repo_root / ".git").exists() or not shutil.which("git"):
        return ""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS,
        )
        if head.returncode != 0:
            return ""
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def collect_versions(ddjvu_command: str = "ddjvu", frontend: dict | None = None) -> dict:
    """
    Everything the About page reports.

    Parameters
    ----------
    ddjvu_command : str
        The configured ``ddjvu_command``.
    frontend : dict, optional
        Front-end library versions, single-sourced by the web app.

    Returns
    -------
    dict
        Keys ``archivum`` (the version), ``git`` (commit or ``""``),
        ``python``, ``packages``, ``frontend``, and ``tools``.
    """
    return {
        "archivum": __version__,
        "git": git_revision(),
        "python": platform.python_version(),
        "packages": package_versions(),
        "frontend": dict(frontend or {}),
        "tools": tool_versions(ddjvu_command),
    }
