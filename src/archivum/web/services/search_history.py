"""Search-box history built from the searches the server receives.

The Query box searches as you type, so a history saved on Enter stays nearly
empty. Instead each search route notes its text here: the latest text per box
is held as a pending entry and filed into the history once the typing has
stopped. A burst of typing therefore files one entry, its final text.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import g

logger = logging.getLogger(__name__)

QUIET_SECONDS = 10.0
LIMIT = 200
BOXES = ("query", "ripgrep", "network")
COLUMNS = ("box", "text", "last_used", "uses")


class SearchHistory:
    """
    Per-box search history kept in one CSV file, newest first.

    Parameters
    ----------
    path : Path
        The CSV file, with columns ``box,text,last_used,uses``. A missing
        file is an empty history; it is read on first use.
    clock : callable, optional
        Returns the current time in seconds since the epoch; replaced in tests.

    Notes
    -----
    A pending entry is filed when the next search for its box arrives more
    than ``QUIET_SECONDS`` later, when the box is cleared (an empty search),
    or when the box's history is requested. Filing happens inside that call;
    there is no timer thread, so a pending entry is lost if the server stops
    first. Filing a text already listed moves it to the front and adds one to
    its ``uses``. One lock guards everything, because waitress serves
    requests on several threads.
    """

    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, list[dict]] | None = None
        self._pending: dict[str, tuple[str, float]] = {}

    def note(self, box: str, text: str) -> None:
        """
        Record a search for ``box``, filing the pending entry if it is due.

        Parameters
        ----------
        box : str
            One of ``BOXES``.
        text : str
            The search text; empty means the box was cleared.
        """
        text = (text or "").strip()
        with self._lock:
            now = self.clock()
            pending = self._pending.get(box)
            if pending and (not text or now - pending[1] > QUIET_SECONDS):
                self._file(box)
            if text:
                self._pending[box] = (text, now)
            else:
                self._pending.pop(box, None)

    def entries(self, box: str) -> list[str]:
        """
        Return the history for ``box``, newest first, after filing its pending entry.

        Parameters
        ----------
        box : str
            One of ``BOXES``.

        Returns
        -------
        list of str
            The filed search texts.
        """
        with self._lock:
            if box in self._pending:
                self._file(box)
            return [row["text"] for row in self._loaded().get(box, [])]

    def _loaded(self) -> dict[str, list[dict]]:
        """Return the rows by box, reading the CSV on first use."""
        if self._rows is None:
            rows: dict[str, list[dict]] = {}
            if self.path.exists():
                with self.path.open(newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        row["uses"] = int(row["uses"])
                        rows.setdefault(row["box"], []).append(row)
            self._rows = rows
        return self._rows

    def _file(self, box: str) -> None:
        """Move the pending entry for ``box`` to the front of its history and save."""
        text, when = self._pending.pop(box)
        rows = self._loaded().setdefault(box, [])
        uses = 1
        for i, row in enumerate(rows):
            if row["text"] == text:
                uses += rows.pop(i)["uses"]
                break
        last_used = datetime.fromtimestamp(when).isoformat(timespec="seconds")
        rows.insert(0, {"box": box, "text": text, "last_used": last_used, "uses": uses})
        del rows[LIMIT:]
        self._save()

    def _save(self) -> None:
        """Rewrite the CSV atomically: a sibling temp file, then ``os.replace``."""
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            with temp.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                for rows in self._rows.values():
                    writer.writerows(rows)
            os.replace(temp, self.path)
        except OSError:
            # e.g. the CSV is open in Excel; memory stays ahead and the next filing retries
            logger.warning("Could not save search history to %s", self.path, exc_info=True)


_INSTANCES: dict[Path, SearchHistory] = {}
_INSTANCES_LOCK = threading.Lock()


def history_file(lib) -> Path:
    """Return the library's search-history CSV, beside ``read.feather``."""
    return Path(lib.config_path) / "search-history.csv"


def get_search_history(lib) -> SearchHistory:
    """
    Return the one ``SearchHistory`` for the library's history file.

    Cached by path, so a library reload keeps its pending entries.
    """
    path = history_file(lib)
    with _INSTANCES_LOCK:
        if path not in _INSTANCES:
            _INSTANCES[path] = SearchHistory(path)
        return _INSTANCES[path]


def note_search(lib, box: str, text: str) -> None:
    """
    Note a search from a route; only admin requests are recorded.

    Bridge visitors (``g.is_admin`` false) leave no trace. A history failure
    is logged and never fails the search.
    """
    if not getattr(g, "is_admin", False):
        return
    try:
        get_search_history(lib).note(box, text)
    except Exception:
        logger.warning("Search history note failed for box %s", box, exc_info=True)
