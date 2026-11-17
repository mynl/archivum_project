"""
The runner: collects from the public sources into ``<library>/doi-audit/``.

One run at a time per library, started by a person from the Audit page (a
thread in the ``serve`` process) or from a terminal (``doi-audit-run``). The
runner writes only inside ``doi-audit/``: the journal files, ``run-state.json``
and, once at the end, ``cites.feather``. It never writes ``ref.feather``, and
the library watcher does not react to anything in the folder, so no write here
can start further work.

A run is resumed by starting it again: a tag with a fresh result in the
journal is not asked about twice.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import psutil

from .. import egress
from . import journal, sources
from .changesets import cell_value
from .doi import ATTENTION, classify_stored_doi, doi_key

logger = logging.getLogger(__name__)

RUN_LOCK, RUN_STATE, RUN_STOP = "run.lock", "run-state.json", "run.stop"
STATE_WRITE_SECONDS = 10.0
RUNNING, FINISHED, STOPPED, FAILED = "running", "finished", "stopped", "failed"
# seconds a request takes beyond its pacing interval, for the estimate only
ASSUMED_LATENCY_SECONDS = 0.5

# runs started in this process, by library folder
_ACTIVE: dict[Path, "CountsRun"] = {}
_ACTIVE_LOCK = threading.Lock()


class RunActive(RuntimeError):
    """Another run holds the library's ``run.lock``."""


@dataclass
class CountsPlan:
    """
    What a citation-count run over a subset would do.

    Attributes
    ----------
    subset : int
        References in the subset.
    no_doi : int
        References with no DOI.
    unusable : int
        References whose DOI is not a DOI (class ``attention``).
    fresh : int
        References with a result newer than the refresh period; skipped.
    work : dict
        ``doi_key -> [tags]`` still to ask about. References sharing a DOI
        are asked about once.
    """

    subset: int = 0
    no_doi: int = 0
    unusable: int = 0
    fresh: int = 0
    work: dict[str, list[str]] = field(default_factory=dict)

    @property
    def tags(self) -> int:
        """The number of references to ask about."""
        return sum(len(tags) for tags in self.work.values())

    @property
    def batches(self) -> list[list[str]]:
        """The DOIs that can share a request, in lists of at most 100."""
        keys = [k for k in self.work if sources.can_batch(k)]
        return [keys[i:i + sources.OPENALEX_BATCH] for i in range(0, len(keys), sources.OPENALEX_BATCH)]

    @property
    def singles(self) -> list[str]:
        """The DOIs that must be asked about one at a time."""
        return [k for k in self.work if not sources.can_batch(k)]

    @property
    def requests(self) -> int:
        """The number of requests the run will make."""
        return len(self.batches) + len(self.singles)

    def seconds(self, mode: str) -> float:
        """
        Estimate the run's duration in a mode.

        Notes
        -----
        With :math:`b` batch requests at interval :math:`s`, :math:`n` single
        requests at interval :math:`u`, latency :math:`\\ell` per request and
        :math:`k` requests at once, the estimate is
        :math:`\\max(bs + nu,\\ (b + n)\\ell / k)`: the pacing or the latency,
        whichever binds.
        """
        pace = egress.MODES[mode]
        paced = len(self.batches) * pace.search_interval + len(self.singles) * pace.single_interval
        return max(paced, self.requests * ASSUMED_LATENCY_SECONDS / pace.concurrency)


def plan_counts(library_path: Path, rows: pd.DataFrame, refresh_days: float) -> CountsPlan:
    """
    Work out what a citation-count run over ``rows`` would ask.

    Parameters
    ----------
    library_path : Path
        The library folder, for the journal.
    rows : pandas.DataFrame
        The references of the subset.
    refresh_days : float
        A result newer than this is fresh and its reference is skipped.

    Returns
    -------
    CountsPlan
    """
    plan = CountsPlan(subset=len(rows))
    fresh = journal.fresh_count_keys(library_path, refresh_days)
    dois = rows["doi"] if "doi" in rows.columns else [None] * len(rows)
    for tag, doi in zip(rows["tag"], dois):
        doi = cell_value(doi)
        if not doi or not doi.strip():
            plan.no_doi += 1
        elif classify_stored_doi(doi) == ATTENTION:
            plan.unusable += 1
        elif (tag, doi_key(doi)) in fresh:
            plan.fresh += 1
        else:
            plan.work.setdefault(doi_key(doi), []).append(tag)
    return plan


def _path(library_path: Path, name: str) -> Path:
    return journal.audit_dir(library_path) / name


def _write_json(path: Path, content: dict) -> None:
    """Write a small JSON file whole: to a temporary file, then moved into place."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(content, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def lock_holder(library_path: Path) -> dict | None:
    """
    Return the content of ``run.lock`` if its run is alive, else ``None``.

    A lock is alive when its process exists and, if that process is this one,
    a run for the library is registered here. A lock left by a process that
    died is not alive, and the next run replaces it.
    """
    library_path = Path(library_path)
    lock = _read_json(_path(library_path, RUN_LOCK))
    if not lock:
        return None
    pid = lock.get("pid")
    if pid == os.getpid():
        with _ACTIVE_LOCK:
            return lock if library_path in _ACTIVE else None
    return lock if isinstance(pid, int) and psutil.pid_exists(pid) else None


def read_state(library_path: Path) -> dict | None:
    """
    Return the progress of the newest run, or ``None`` if there has been none.

    A run in this process answers from memory; any other from
    ``run-state.json``, which its runner rewrites at most every
    :data:`STATE_WRITE_SECONDS`. A state left ``running`` by a process that
    died is reported as ``failed``.
    """
    library_path = Path(library_path)
    with _ACTIVE_LOCK:
        run = _ACTIVE.get(library_path)
    if run is not None:
        return run.snapshot()
    state = _read_json(_path(library_path, RUN_STATE))
    if state and state.get("status") == RUNNING and lock_holder(library_path) is None:
        state["status"], state["message"] = FAILED, "the process running it ended without finishing"
    return state


def request_stop(library_path: Path) -> bool:
    """
    Ask the library's run to stop after the request in flight.

    Works across processes: a ``run.stop`` file is written, which the runner
    looks for before each request. A run in this process is also told
    directly, which ends a wait at once.

    Returns
    -------
    bool
        False when no run is active.
    """
    library_path = Path(library_path)
    if lock_holder(library_path) is None:
        return False
    _path(library_path, RUN_STOP).write_text(journal.now_utc(), encoding="utf-8")
    with _ACTIVE_LOCK:
        run = _ACTIVE.get(library_path)
    if run is not None:
        run.stop.set()
    return True


class CountsRun:
    """
    One citation-count run for one library.

    Creating the object takes the lock, so a second run is refused at once,
    in the caller's own thread.

    Parameters
    ----------
    library_path : Path
        The library folder.
    plan : CountsPlan
        From :func:`plan_counts`.
    mode : str
        ``egress.SPEEDY`` or ``egress.POLITE``.
    client : egress.Egress, optional
        Replaced in tests. The default is a new client in ``mode``, whose
        waits end when the run is stopped.
    on_progress : callable, optional
        Called with the state after each request, for a terminal.

    Raises
    ------
    RunActive
        If a live run holds the lock.
    """

    def __init__(self, library_path: Path, plan: CountsPlan, mode: str, *,
                 client: egress.Egress | None = None, on_progress=None):
        self.library_path = Path(library_path)
        self.plan = plan
        self.mode = mode
        self.stop = client.stop if client is not None else threading.Event()
        self.client = client or egress.Egress(mode, stop=self.stop)
        self.on_progress = on_progress
        self.run_id = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._last_write = 0.0
        self.state = {
            "run_id": self.run_id, "action": "counts", "mode": mode, "pid": os.getpid(),
            "status": RUNNING, "message": "", "started": journal.now_utc(), "updated": journal.now_utc(),
            "total": plan.tags, "done": 0, "found": 0, "not_found": 0, "errors": 0,
            "requests_planned": plan.requests, "requests": 0, "hosts": {},
        }
        self._acquire()

    def _acquire(self) -> None:
        folder = journal.audit_dir(self.library_path)
        folder.mkdir(parents=True, exist_ok=True)
        with _ACTIVE_LOCK:
            holder = None
            lock = _read_json(folder / RUN_LOCK)
            if lock:
                pid = lock.get("pid")
                alive = (self.library_path in _ACTIVE) if pid == os.getpid() else (
                    isinstance(pid, int) and psutil.pid_exists(pid))
                holder = lock if alive else None
            if holder:
                raise RunActive(f"A run started {holder.get('started')} is active (process {holder.get('pid')}).")
            _write_json(folder / RUN_LOCK, {"pid": os.getpid(), "run_id": self.run_id, "started": self.state["started"]})
            _ACTIVE[self.library_path] = self
        (folder / RUN_STOP).unlink(missing_ok=True)
        self._write_state(force=True)

    def snapshot(self) -> dict:
        """Return a copy of the run's progress."""
        with self._lock:
            return dict(self.state)

    def _write_state(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_write < STATE_WRITE_SECONDS:
                return
            self._last_write = now
            self.state["updated"] = journal.now_utc()
            self.state["hosts"] = self.client.summary()
            content = dict(self.state)
        _write_json(_path(self.library_path, RUN_STATE), content)

    def _stop_requested(self) -> bool:
        if not self.stop.is_set() and _path(self.library_path, RUN_STOP).exists():
            self.stop.set()
        return self.stop.is_set()

    def _record(self, keys: list[str], found: dict[str, dict], error: str = "") -> None:
        """Append the journal lines for the DOIs of one request, and count them."""
        observations, counts = [], []
        tally = {"found": 0, "not_found": 0, "errors": 0}
        for key in keys:
            record = found.get(key)
            for tag in self.plan.work[key]:
                base = {"run_id": self.run_id, "tag": tag, "source": "openalex", "query_kind": "doi", "query": key}
                if error:
                    observations.append({**base, "status": journal.ERROR, "error": error})
                    tally["errors"] += 1
                elif record is None:
                    observations.append({**base, "status": journal.NOT_FOUND})
                    tally["not_found"] += 1
                else:
                    observations.append({**base, "status": journal.OK, **record})
                    counts.append({"tag": tag, "source": "openalex", "source_id": record["source_id"],
                                   "doi": key, "count": record["cited_by_count"]})
                    tally["found"] += 1
        journal.append_lines(self.library_path, journal.OBSERVATIONS, observations)
        journal.append_lines(self.library_path, journal.COUNTS, counts)
        with self._lock:
            self.state["requests"] += 1
            self.state["done"] += sum(tally.values())
            for name, n in tally.items():
                self.state[name] += n

    def _ask(self, keys: list[str], batch: bool) -> None:
        """Make one request and record its answer. A stopped run makes no request."""
        if self._stop_requested():
            return
        try:
            if batch:
                found = sources.openalex_works_by_doi(self.client, keys)
            else:
                record = sources.openalex_work(self.client, f"doi:{keys[0]}")
                found = {keys[0]: record} if record else {}
            self._record(keys, found)
        except egress.Stopped:
            return
        except egress.HostStopped as e:
            # nothing more can be asked of the host, so the run ends here
            with self._lock:
                self.state["message"] = str(e)
            self.stop.set()
        except (sources.SourceError, ValueError, KeyError) as e:
            logger.error("DOI audit request failed: %s", e)
            self._record(keys, {}, error=str(e))
        self._write_state()
        if self.on_progress:
            self.on_progress(self.snapshot())

    def run(self) -> dict:
        """
        Do the run in the calling thread, and return the final state.

        Requests go out through a pool of as many threads as the mode allows
        at once; the pace itself is enforced in ``egress``. Ctrl-C in a
        terminal stops the run after the requests in flight. Whatever happens
        the journal lines already written stay, ``cites.feather`` is rebuilt,
        and the lock is released.
        """
        status = FAILED
        try:
            jobs = [(keys, True) for keys in self.plan.batches] + [([key], False) for key in self.plan.singles]
            with ThreadPoolExecutor(max_workers=egress.MODES[self.mode].concurrency,
                                    thread_name_prefix="doi-audit") as pool:
                futures = [pool.submit(self._ask, keys, batch) for keys, batch in jobs]
                try:
                    for future in as_completed(futures):
                        future.result()
                except KeyboardInterrupt:
                    self.stop.set()
            status = STOPPED if self.stop.is_set() else FINISHED
        except Exception as e:
            logger.error("DOI audit run failed: %s", e, exc_info=True)
            with self._lock:
                self.state["message"] = f"{type(e).__name__}: {e}"
        finally:
            try:
                journal.derive_cites(self.library_path)
            except Exception as e:
                logger.error("Could not rebuild cites.feather: %s", e, exc_info=True)
                status = FAILED
                with self._lock:
                    self.state["message"] = f"cites.feather not rebuilt: {e}"
            with self._lock:
                self.state["status"] = status
                self.state["finished"] = journal.now_utc()
            self._write_state(force=True)
            with _ACTIVE_LOCK:
                _ACTIVE.pop(self.library_path, None)
            _path(self.library_path, RUN_LOCK).unlink(missing_ok=True)
            _path(self.library_path, RUN_STOP).unlink(missing_ok=True)
        return self.snapshot()

    def start(self) -> threading.Thread:
        """Do the run in a daemon thread, for the web process, and return the thread."""
        thread = threading.Thread(target=self.run, name=f"doi-audit-run-{self.run_id}", daemon=True)
        thread.start()
        return thread


def format_duration(seconds: float) -> str:
    """Return a duration for a person: ``12 s``, ``3 min``, ``1 h 20 min``."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{max(1, round(seconds))} s"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def elapsed_seconds(state: dict) -> float:
    """Return how long a run has taken: to its finish, or to now while it runs."""
    start = dt.datetime.fromisoformat(state["started"])
    end = dt.datetime.fromisoformat(state["finished"]) if state.get("finished") else dt.datetime.now(dt.timezone.utc)
    return (end - start).total_seconds()
