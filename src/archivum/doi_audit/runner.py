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
from .doi import (
    ATTENTION, NOT_FOUND, VERIFIED, YEAR_DIFFERS, arxiv_id, classify_candidates, classify_stored_doi, doi_key,
    first_surname, fold, is_arxiv_doi, match,
)

logger = logging.getLogger(__name__)

RUN_LOCK, RUN_STATE, RUN_STOP = "run.lock", "run-state.json", "run.stop"
STATE_WRITE_SECONDS = 10.0
RUNNING, FINISHED, STOPPED, FAILED = "running", "finished", "stopped", "failed"
# seconds a request takes beyond its pacing interval, for the estimate only
ASSUMED_LATENCY_SECONDS = 0.5

# runs started in this process, by library folder
_ACTIVE: dict[Path, "Run"] = {}
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
    works : dict
        ``OpenAlex work id -> [tags]`` still to ask about: references with no
        DOI whose work was accepted in review.
    second : dict
        ``doi_key -> [tags]`` to ask Semantic Scholar about for a second
        count; empty unless that was asked for.
    """

    subset: int = 0
    no_doi: int = 0
    unusable: int = 0
    fresh: int = 0
    work: dict[str, list[str]] = field(default_factory=dict)
    works: dict[str, list[str]] = field(default_factory=dict)
    second: dict[str, list[str]] = field(default_factory=dict)

    @property
    def tags(self) -> int:
        """The number of references to ask about, of any source."""
        asked = {tag for group in (self.work, self.works, self.second) for tags in group.values() for tag in tags}
        return len(asked)

    @property
    def second_batches(self) -> list[list[str]]:
        """The DOIs for Semantic Scholar, in lists of at most 500."""
        keys = list(self.second)
        size = sources.SEMANTIC_SCHOLAR_BATCH
        return [keys[i:i + size] for i in range(0, len(keys), size)]

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
        return len(self.batches) + len(self.singles) + len(self.works) + len(self.second_batches)

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
        paced = ((len(self.batches) + len(self.second_batches)) * pace.search_interval
                 + (len(self.singles) + len(self.works)) * pace.single_interval)
        return max(paced, self.requests * ASSUMED_LATENCY_SECONDS / pace.concurrency)


def plan_counts(library_path: Path, rows: pd.DataFrame, refresh_days: float, second: bool = False) -> CountsPlan:
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
    second : bool, optional
        Whether to ask Semantic Scholar too, for a second count of each DOI.
        Its results have their own freshness.

    Returns
    -------
    CountsPlan
    """
    plan = CountsPlan(subset=len(rows))
    fresh = journal.fresh_count_keys(library_path, refresh_days)
    fresh_second = journal.fresh_count_keys(library_path, refresh_days, source="semanticscholar") if second else set()
    accepted = journal.accepted_works(library_path)
    dois = rows["doi"] if "doi" in rows.columns else [None] * len(rows)
    for tag, doi in zip(rows["tag"], dois):
        doi = cell_value(doi)
        if not doi or not doi.strip():
            # no DOI: counted only through a work the user accepted for it
            if tag not in accepted:
                plan.no_doi += 1
            elif (tag, f"work:{accepted[tag]}") in fresh:
                plan.fresh += 1
            else:
                plan.works.setdefault(accepted[tag], []).append(tag)
        elif classify_stored_doi(doi) == ATTENTION:
            plan.unusable += 1
        else:
            key = doi_key(doi)
            if second and (tag, key) not in fresh_second:
                plan.second.setdefault(key, []).append(tag)
            if (tag, key) in fresh:
                plan.fresh += 1
            else:
                plan.work.setdefault(key, []).append(tag)
    return plan


OPENALEX_STAGE, CROSSREF_STAGE, DOI_ORG_STAGE = "openalex", "crossref", "doi.org"


def next_stage(reference: dict, key: str, latest: dict, refresh_days: float) -> str | None:
    """
    The next source to ask about a reference's DOI, or ``None`` when it is settled.

    Parameters
    ----------
    reference : dict
        The library's ``tag``, ``title``, ``author`` and ``year``.
    key : str
        The DOI in compared form.
    latest : dict
        From :func:`journal.latest_observations`.
    refresh_days : float
        An answer older than this does not count.

    Returns
    -------
    str or None
        ``openalex`` first. Then ``crossref``, unless the OpenAlex record
        already verifies the DOI. Then ``doi.org``, only when neither has the
        DOI: a record from either source shows that it exists, and ``doi.org``
        is the one place that knows a DOI registered elsewhere.

    Notes
    -----
    The order keeps requests few: OpenAlex answers 100 DOIs in a request, the
    other two one each, and most DOIs are settled by the first.
    """
    tag = reference["tag"]
    openalex = latest.get((tag, "openalex", "doi", key))
    if not journal.is_fresh(openalex, refresh_days):
        return OPENALEX_STAGE
    if openalex["status"] == journal.OK and match(reference, openalex).cls in (VERIFIED, YEAR_DIFFERS):
        return None
    crossref = latest.get((tag, "crossref", "doi", key))
    if not journal.is_fresh(crossref, refresh_days):
        return CROSSREF_STAGE
    if openalex["status"] == journal.OK or crossref["status"] == journal.OK:
        return None
    if not journal.is_fresh(latest.get((tag, "doi.org", "doi", key)), refresh_days):
        return DOI_ORG_STAGE
    return None


@dataclass
class CheckPlan:
    """
    What a DOI check over a subset would do.

    Attributes
    ----------
    subset, no_doi, unusable, fresh : int
        As in :class:`CountsPlan`; ``fresh`` counts references already settled.
    references : dict
        ``tag -> {tag, title, author, year, key}`` for the references still to
        settle.
    stages : dict
        ``tag -> stage`` each of them is at.
    refresh_days : float
        The refresh period the plan was made with.
    """

    subset: int = 0
    no_doi: int = 0
    unusable: int = 0
    fresh: int = 0
    references: dict[str, dict] = field(default_factory=dict)
    stages: dict[str, str] = field(default_factory=dict)
    refresh_days: float = 30.0

    @property
    def tags(self) -> int:
        """The number of references to settle."""
        return len(self.references)

    def keys_at(self, stage: str) -> dict[str, list[str]]:
        """``doi_key -> [tags]`` for the references at a stage; a shared DOI is asked about once."""
        work: dict[str, list[str]] = {}
        for tag, at in self.stages.items():
            if at == stage:
                work.setdefault(self.references[tag]["key"], []).append(tag)
        return work

    @property
    def openalex_requests(self) -> int:
        """Requests to OpenAlex: batches of 100, and one each for DOIs that cannot be batched."""
        keys = list(self.keys_at(OPENALEX_STAGE))
        batched = sum(1 for k in keys if sources.can_batch(k))
        return -(-batched // sources.OPENALEX_BATCH) + (len(keys) - batched)

    @property
    def requests(self) -> int:
        """The fewest requests the run can make: every DOI not yet asked about is verified by OpenAlex."""
        return self.openalex_requests + len(self.keys_at(CROSSREF_STAGE)) + len(self.keys_at(DOI_ORG_STAGE))

    @property
    def requests_most(self) -> int:
        """The most requests the run can make: every DOI goes on to Crossref and to ``doi.org``."""
        return (self.openalex_requests + 2 * len(self.keys_at(OPENALEX_STAGE))
                + 2 * len(self.keys_at(CROSSREF_STAGE)) + len(self.keys_at(DOI_ORG_STAGE)))

    def seconds(self, mode: str, requests: int | None = None) -> float:
        """Estimate the duration in a mode for a number of single requests; the fewest by default."""
        pace = egress.MODES[mode]
        requests = self.requests if requests is None else requests
        return max(requests * pace.single_interval, requests * ASSUMED_LATENCY_SECONDS / pace.concurrency)


def plan_check(library_path: Path, rows: pd.DataFrame, refresh_days: float) -> CheckPlan:
    """
    Work out what a DOI check over ``rows`` would ask.

    Parameters
    ----------
    library_path : Path
        The library folder, for the journal.
    rows : pandas.DataFrame
        The references of the subset.
    refresh_days : float
        An answer newer than this is fresh and is not asked for again.

    Returns
    -------
    CheckPlan
    """
    plan = CheckPlan(subset=len(rows), refresh_days=refresh_days)
    latest = journal.latest_observations(library_path)
    for row in rows.to_dict("records"):
        doi = cell_value(row.get("doi"))
        if not doi or not doi.strip():
            plan.no_doi += 1
            continue
        if classify_stored_doi(doi) == ATTENTION:
            plan.unusable += 1
            continue
        reference = {"tag": row["tag"], "title": cell_value(row.get("title")), "author": cell_value(row.get("author")),
                     "year": cell_value(row.get("year")), "key": doi_key(doi)}
        stage = next_stage(reference, reference["key"], latest, refresh_days)
        if stage is None:
            plan.fresh += 1
        else:
            plan.references[row["tag"]] = reference
            plan.stages[row["tag"]] = stage
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


class Run:
    """
    One run of the runner for one library: the lock, the progress, the pool.

    Creating the object takes the lock, so a second run is refused at once,
    in the caller's own thread. A subclass says what to ask by overriding
    :meth:`phases`.

    Parameters
    ----------
    library_path : Path
        The library folder.
    mode : str
        ``egress.SPEEDY`` or ``egress.POLITE``.
    total : int
        The references the run is to settle.
    requests_planned : int
        The requests expected, as far as is known at the start.
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

    action = ""

    def __init__(self, library_path: Path, mode: str, *, total: int, requests_planned: int,
                 client: egress.Egress | None = None, on_progress=None):
        self.library_path = Path(library_path)
        self.mode = mode
        self.stop = client.stop if client is not None else threading.Event()
        self.client = client or egress.Egress(mode, stop=self.stop)
        self.on_progress = on_progress
        self.run_id = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._last_write = 0.0
        self.state = {
            "run_id": self.run_id, "action": self.action, "mode": mode, "pid": os.getpid(),
            "status": RUNNING, "message": "", "phase": "", "started": journal.now_utc(), "updated": journal.now_utc(),
            "total": total, "done": 0, "found": 0, "not_found": 0, "errors": 0,
            "requests_planned": requests_planned, "requests": 0, "hosts": {},
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

    def tally(self, **counts: int) -> None:
        """Add to the run's counters (``done``, ``found``, ``not_found``, ``errors``, ``requests``)."""
        with self._lock:
            for name, n in counts.items():
                self.state[name] += n

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

    def phases(self):
        """
        Yield ``(name, jobs)`` for each phase of the run, in order.

        A job is ``(ask, on_error)``: ``ask()`` makes one request and records
        its answer; ``on_error(message)`` records a failure of that request.
        The jobs of a phase run on the pool. The code between two yields runs
        after the earlier phase has finished, so a later phase can depend on
        what an earlier one found.
        """
        raise NotImplementedError

    def _do(self, ask, on_error) -> None:
        """Run one job. A stopped run makes no request."""
        if self._stop_requested():
            return
        try:
            ask()
        except egress.Stopped:
            return
        except egress.HostStopped as e:
            # nothing more can be asked of the host, so the run ends here
            with self._lock:
                self.state["message"] = str(e)
            self.stop.set()
        except (sources.SourceError, ValueError, KeyError) as e:
            logger.error("DOI audit request failed: %s", e)
            on_error(str(e))
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
            with ThreadPoolExecutor(max_workers=egress.MODES[self.mode].concurrency,
                                    thread_name_prefix="doi-audit") as pool:
                try:
                    for name, jobs in self.phases():
                        with self._lock:
                            self.state["phase"] = name
                            self.state["requests_planned"] = max(
                                self.state["requests_planned"], self.state["requests"] + len(jobs))
                        for future in as_completed([pool.submit(self._do, ask, on_error) for ask, on_error in jobs]):
                            future.result()
                        if self._stop_requested():
                            break
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

    # ---- OpenAlex by DOI, shared by the count run and the DOI check ----

    def _openalex_jobs(self, work: dict[str, list[str]], settled=None) -> list[tuple]:
        """
        Jobs asking OpenAlex about the DOIs in ``work`` (``doi_key -> [tags]``).

        Each answer is written to ``observations.jsonl``, and each count to
        ``counts.jsonl``. ``settled(tag, record)`` says whether an answer
        settles its reference, for the ``done`` counter; by default any answer
        does.
        """
        batchable = [k for k in work if sources.can_batch(k)]
        batches = [batchable[i:i + sources.OPENALEX_BATCH] for i in range(0, len(batchable), sources.OPENALEX_BATCH)]
        singles = [[k] for k in work if not sources.can_batch(k)]

        def ask(keys, batch):
            if batch:
                found = sources.openalex_works_by_doi(self.client, keys)
            else:
                record = sources.openalex_work(self.client, f"doi:{keys[0]}")
                found = {keys[0]: record} if record else {}
            self._record_openalex(work, keys, found, settled=settled)

        return [(lambda keys=keys, batch=batch: ask(keys, batch),
                 lambda message, keys=keys: self._record_openalex(work, keys, {}, error=message))
                for keys, batch in [(b, True) for b in batches] + [(s, False) for s in singles]]

    def _record_openalex(self, work, keys, found, error: str = "", settled=None) -> None:
        """Append the journal lines for the DOIs of one OpenAlex request, and count them."""
        observations, counts = [], []
        tally = {"found": 0, "not_found": 0, "errors": 0, "done": 0}
        for key in keys:
            record = found.get(key)
            for tag in work[key]:
                base = {"run_id": self.run_id, "tag": tag, "source": "openalex", "query_kind": "doi", "query": key}
                if error:
                    observations.append({**base, "status": journal.ERROR, "error": error})
                    tally["errors"] += 1
                    tally["done"] += 1
                    continue
                if record is None:
                    observations.append({**base, "status": journal.NOT_FOUND})
                    tally["not_found"] += 1
                else:
                    observations.append({**base, "status": journal.OK, **record})
                    counts.append({"tag": tag, "source": "openalex", "source_id": record["source_id"],
                                   "doi": key, "count": record["cited_by_count"]})
                    tally["found"] += 1
                self._answered(observations[-1])
                if settled is None or settled(tag, record):
                    tally["done"] += 1
        journal.append_lines(self.library_path, journal.OBSERVATIONS, observations)
        journal.append_lines(self.library_path, journal.COUNTS, counts)
        self.tally(requests=1, **tally)

    def _answered(self, observation: dict) -> None:
        """Hook: an answer has arrived. A run that plans later phases from earlier answers keeps it."""

    def _stage(self, tag: str) -> str | None:
        """Hook: the stage a reference is at, ``None`` when it is settled. A run with one phase settles everything."""
        return None

    def _single_jobs(self, source: str, work: dict[str, list[str]], ask_source) -> list[tuple]:
        """Jobs asking ``source`` about one DOI each; ``ask_source(key)`` returns a record, or ``None`` for not found."""
        def record(key, answer, error=""):
            observations = []
            for tag in work[key]:
                base = {"run_id": self.run_id, "tag": tag, "source": source, "query_kind": "doi", "query": key}
                if error:
                    observations.append({**base, "status": journal.ERROR, "error": error})
                    continue
                observations.append({**base, "status": journal.OK, **answer} if answer is not None
                                    else {**base, "status": journal.NOT_FOUND})
                self._answered(observations[-1])
            journal.append_lines(self.library_path, journal.OBSERVATIONS, observations)
            tags = work[key]
            settled = len(tags) if error else sum(self._stage(tag) is None for tag in tags)
            self.tally(requests=1, done=settled, errors=len(tags) if error else 0,
                       found=0 if error or answer is None else len(tags),
                       not_found=len(tags) if not error and answer is None else 0)

        return [(lambda key=key: record(key, ask_source(key)), lambda message, key=key: record(key, None, error=message))
                for key in work]


class CountsRun(Run):
    """
    A citation-count run: OpenAlex is asked about each DOI in the plan.

    Parameters
    ----------
    library_path : Path
        The library folder.
    plan : CountsPlan
        From :func:`plan_counts`.
    mode : str
        ``egress.SPEEDY`` or ``egress.POLITE``.
    client, on_progress
        As for :class:`Run`.
    """

    action = "counts"

    def __init__(self, library_path: Path, plan: CountsPlan, mode: str, *,
                 client: egress.Egress | None = None, on_progress=None):
        self.plan = plan
        super().__init__(library_path, mode, total=plan.tags, requests_planned=plan.requests,
                         client=client, on_progress=on_progress)

    def _work_jobs(self) -> list[tuple]:
        """Jobs asking OpenAlex, by work id, for the count of each accepted work with no DOI."""
        def record(work_id, answer, error=""):
            tags = self.plan.works[work_id]
            base = {"run_id": self.run_id, "source": "openalex", "query_kind": "source_id", "query": work_id}
            if error:
                journal.append_lines(self.library_path, journal.OBSERVATIONS,
                                     [{**base, "tag": tag, "status": journal.ERROR, "error": error} for tag in tags])
                self.tally(requests=1, done=len(tags), errors=len(tags))
                return
            status = journal.OK if answer else journal.NOT_FOUND
            journal.append_lines(self.library_path, journal.OBSERVATIONS,
                                 [{**base, "tag": tag, "status": status, **(answer or {})} for tag in tags])
            if answer:
                journal.append_lines(self.library_path, journal.COUNTS, [
                    {"tag": tag, "source": "openalex", "source_id": work_id, "doi": None,
                     "count": answer["cited_by_count"]} for tag in tags])
            self.tally(requests=1, done=len(tags), found=len(tags) if answer else 0,
                       not_found=0 if answer else len(tags))

        return [(lambda w=w: record(w, sources.openalex_work(self.client, w)),
                 lambda message, w=w: record(w, None, error=message)) for w in self.plan.works]

    def _second_jobs(self) -> list[tuple]:
        """Jobs asking Semantic Scholar for a second count, 500 DOIs a request."""
        def record(batch, found, error=""):
            observations, counts = [], []
            for key in batch:
                answer = found.get(key)
                for tag in self.plan.second[key]:
                    base = {"run_id": self.run_id, "tag": tag, "source": "semanticscholar", "query_kind": "doi", "query": key}
                    if error:
                        observations.append({**base, "status": journal.ERROR, "error": error})
                    elif answer is None:
                        observations.append({**base, "status": journal.NOT_FOUND})
                    else:
                        counts.append({"tag": tag, "source": "semanticscholar", "source_id": answer["source_id"],
                                       "doi": key, "count": answer["cited_by_count"]})
            journal.append_lines(self.library_path, journal.OBSERVATIONS, observations)
            journal.append_lines(self.library_path, journal.COUNTS, counts)
            tags = sum(len(self.plan.second[key]) for key in batch)
            # done counts references once, in the OpenAlex phase; a reference asked only here is counted here
            only_here = sum(1 for key in batch for tag in self.plan.second[key] if tag not in self._first_tags)
            self.tally(requests=1, done=only_here, errors=tags if error else 0)

        return [(lambda batch=batch: record(batch, sources.semantic_scholar_counts(self.client, batch)),
                 lambda message, batch=batch: record(batch, {}, error=message)) for batch in self.plan.second_batches]

    def phases(self):
        self._first_tags = {tag for group in (self.plan.work, self.plan.works) for tags in group.values() for tag in tags}
        yield "OpenAlex", self._openalex_jobs(self.plan.work) + self._work_jobs()
        if self.plan.second:
            yield "Semantic Scholar", self._second_jobs()


class CheckRun(Run):
    """
    A DOI check: each DOI is asked about until it is settled.

    The phases follow :func:`next_stage`. OpenAlex first, in batches, which
    also collects the citation counts. Then Crossref, one DOI a request, for
    the DOIs OpenAlex does not verify. Then ``doi.org``, for the DOIs neither
    has. Each phase is planned from the answers of the one before.

    Parameters
    ----------
    library_path : Path
        The library folder.
    plan : CheckPlan
        From :func:`plan_check`.
    mode : str
        ``egress.SPEEDY`` or ``egress.POLITE``.
    client, on_progress
        As for :class:`Run`.
    """

    action = "check"

    def __init__(self, library_path: Path, plan: CheckPlan, mode: str, *,
                 client: egress.Egress | None = None, on_progress=None):
        self.plan = plan
        # answers so far, kept in memory so later phases are planned without re-reading the journal
        self.latest = dict(journal.latest_observations(library_path))
        self._latest_lock = threading.Lock()
        super().__init__(library_path, mode, total=plan.tags, requests_planned=plan.requests,
                         client=client, on_progress=on_progress)

    def _answered(self, observation: dict) -> None:
        stamped = {"at": journal.now_utc(), **observation}
        with self._latest_lock:
            self.latest[(stamped["tag"], stamped["source"], stamped["query_kind"], stamped["query"])] = stamped

    def _stage(self, tag: str) -> str | None:
        reference = self.plan.references[tag]
        with self._latest_lock:
            return next_stage(reference, reference["key"], self.latest, self.plan.refresh_days)

    def _keys_at(self, stage: str) -> dict[str, list[str]]:
        work: dict[str, list[str]] = {}
        for tag, reference in self.plan.references.items():
            if self._stage(tag) == stage:
                work.setdefault(reference["key"], []).append(tag)
        return work

    def phases(self):
        yield "OpenAlex", self._openalex_jobs(
            self.plan.keys_at(OPENALEX_STAGE), settled=lambda tag, record: self._stage(tag) is None)
        yield "Crossref", self._single_jobs(
            "crossref", self._keys_at(CROSSREF_STAGE), lambda key: sources.crossref_work(self.client, key))
        yield "doi.org", self._single_jobs(
            "doi.org", self._keys_at(DOI_ORG_STAGE),
            lambda key: {"exists": True} if sources.doi_exists(self.client, key) else None)


CROSSREF_SEARCH, OPENALEX_SEARCH = "crossref", "openalex"


def next_search_stage(reference: dict, latest: dict, search_days: float) -> str | None:
    """
    The next source to search for a reference's DOI, or ``None`` when it is settled.

    Parameters
    ----------
    reference : dict
        The library's ``tag``, ``title``, ``author``, ``year``, and ``key``,
        the folded title, which is what a search is filed under.
    latest : dict
        From :func:`journal.latest_observations`.
    search_days : float
        A search older than this does not count.

    Returns
    -------
    str or None
        ``crossref`` first, since it is free. Then ``openalex``, only when
        Crossref's candidates class as not found: an OpenAlex search costs ten
        times a DOI lookup against the day's budget.
    """
    tag, key = reference["tag"], reference["key"]
    crossref = latest.get((tag, "crossref", "title", key))
    if not journal.is_fresh(crossref, search_days):
        return CROSSREF_SEARCH
    if classify_candidates(reference, crossref.get("candidates") or [])[0] != NOT_FOUND:
        return None
    if not journal.is_fresh(latest.get((tag, "openalex", "title", key)), search_days):
        return OPENALEX_SEARCH
    return None


@dataclass
class SearchPlan:
    """
    What a search for missing DOIs over a subset would do.

    Attributes
    ----------
    subset : int
        References in the subset.
    has_doi : int
        References that have a DOI already; not searched.
    no_title : int
        References with no DOI and no title; nothing to search with.
    fresh : int
        References searched within the search period; skipped.
    references : dict
        ``tag -> {tag, title, author, year, key}`` still to search.
    stages : dict
        ``tag -> stage`` each of them is at.
    search_days : float
        The search period the plan was made with.
    """

    subset: int = 0
    has_doi: int = 0
    no_title: int = 0
    fresh: int = 0
    references: dict[str, dict] = field(default_factory=dict)
    stages: dict[str, str] = field(default_factory=dict)
    search_days: float = 180.0

    @property
    def tags(self) -> int:
        """The number of references to search for."""
        return len(self.references)

    def at(self, stage: str) -> list[str]:
        """The tags at a stage."""
        return [tag for tag, s in self.stages.items() if s == stage]

    @property
    def requests(self) -> int:
        """The fewest requests: Crossref finds something for every reference it is asked about."""
        return len(self.references)

    @property
    def requests_most(self) -> int:
        """The most requests: every reference asked of Crossref goes on to OpenAlex."""
        return len(self.references) + len(self.at(CROSSREF_SEARCH))

    @property
    def openalex_searches(self) -> tuple[int, int]:
        """The fewest and the most OpenAlex searches, each ten credits of the day's budget."""
        return len(self.at(OPENALEX_SEARCH)), len(self.references)

    def seconds(self, mode: str, requests: int | None = None) -> float:
        """Estimate the duration in a mode for a number of searches; the fewest by default."""
        pace = egress.MODES[mode]
        requests = self.requests if requests is None else requests
        return max(requests * pace.search_interval, requests * ASSUMED_LATENCY_SECONDS / pace.concurrency)


def plan_search(library_path: Path, rows: pd.DataFrame, search_days: float) -> SearchPlan:
    """
    Work out what a search for missing DOIs over ``rows`` would ask.

    Parameters
    ----------
    library_path : Path
        The library folder, for the journal.
    rows : pandas.DataFrame
        The references of the subset.
    search_days : float
        A search newer than this is not repeated, whatever it found.

    Returns
    -------
    SearchPlan
    """
    plan = SearchPlan(subset=len(rows), search_days=search_days)
    latest = journal.latest_observations(library_path)
    for row in rows.to_dict("records"):
        doi = cell_value(row.get("doi"))
        if doi and doi.strip():
            plan.has_doi += 1
            continue
        title = cell_value(row.get("title"))
        if not title or not fold(title):
            plan.no_title += 1
            continue
        reference = {"tag": row["tag"], "title": title, "author": cell_value(row.get("author")),
                     "year": cell_value(row.get("year")), "key": fold(title)}
        stage = next_search_stage(reference, latest, search_days)
        if stage is None:
            plan.fresh += 1
        else:
            plan.references[row["tag"]] = reference
            plan.stages[row["tag"]] = stage
    return plan


class SearchRun(Run):
    """
    A search for missing DOIs: each reference with no DOI is searched for by title.

    The phases follow :func:`next_search_stage`: Crossref for every reference,
    then OpenAlex for those Crossref found nothing for. What leaves the
    machine for a reference is its title, its first author's surname and its
    year.

    Parameters
    ----------
    library_path : Path
        The library folder.
    plan : SearchPlan
        From :func:`plan_search`.
    mode : str
        ``egress.SPEEDY`` or ``egress.POLITE``.
    client, on_progress
        As for :class:`Run`.
    """

    action = "search"

    def __init__(self, library_path: Path, plan: SearchPlan, mode: str, *,
                 client: egress.Egress | None = None, on_progress=None):
        self.plan = plan
        self.latest = dict(journal.latest_observations(library_path))
        self._latest_lock = threading.Lock()
        super().__init__(library_path, mode, total=plan.tags, requests_planned=plan.requests,
                         client=client, on_progress=on_progress)

    def _stage(self, tag: str) -> str | None:
        with self._latest_lock:
            return next_search_stage(self.plan.references[tag], self.latest, self.plan.search_days)

    def _search_jobs(self, source: str, tags: list[str], search) -> list[tuple]:
        """Jobs searching ``source`` for one reference each; ``search(reference)`` returns the candidates."""
        def record(tag, candidates, error=""):
            reference = self.plan.references[tag]
            base = {"run_id": self.run_id, "tag": tag, "source": source, "query_kind": "title", "query": reference["key"]}
            if error:
                journal.append_lines(self.library_path, journal.OBSERVATIONS,
                                     [{**base, "status": journal.ERROR, "error": error}])
                self.tally(requests=1, done=1, errors=1)
                return
            observation = {**base, "status": journal.OK if candidates else journal.NOT_FOUND, "candidates": candidates}
            journal.append_lines(self.library_path, journal.OBSERVATIONS, [observation])
            with self._latest_lock:
                self.latest[(tag, source, "title", reference["key"])] = {"at": journal.now_utc(), **observation}
            settled = self._stage(tag) is None
            found = classify_candidates(reference, candidates)[0] != NOT_FOUND
            self.tally(requests=1, done=1 if settled else 0, found=1 if found else 0,
                       not_found=1 if settled and not found else 0)

        return [(lambda tag=tag: record(tag, search(self.plan.references[tag])),
                 lambda message, tag=tag: record(tag, [], error=message)) for tag in tags]

    def phases(self):
        yield "Crossref", self._search_jobs(
            "crossref", self.plan.at(CROSSREF_SEARCH),
            lambda r: sources.crossref_search(self.client, r["title"], first_surname(r["author"]), r["year"] or ""))
        yield "OpenAlex", self._search_jobs(
            "openalex", [tag for tag in self.plan.references if self._stage(tag) == OPENALEX_SEARCH],
            lambda r: sources.openalex_search(self.client, r["title"]))


ARXIV_STAGE = "arxiv"


def reference_arxiv_id(row: dict) -> str | None:
    """
    The arXiv identifier of a reference, from ``arxivId``, ``eprint`` or an arXiv ``url``.

    A ``url`` is read only when it is an arXiv address, since other addresses
    hold numbers of the same shape.
    """
    url = cell_value(row.get("url")) or ""
    return arxiv_id(cell_value(row.get("arxivId")), cell_value(row.get("eprint")),
                    url if "arxiv.org" in url.lower() else None)


def published_doi(reference: dict, latest: dict) -> str | None:
    """
    The DOI of a preprint's published version, as far as is known.

    The DOI the authors gave arXiv, else the reference's own DOI when that is
    not an arXiv one. In compared form; ``None`` when neither exists.
    """
    arxiv = latest.get((reference["tag"], "arxiv", "arxiv_id", reference["arxiv"]))
    given = arxiv.get("doi") if arxiv and arxiv.get("status") == journal.OK else None
    if given and not is_arxiv_doi(given):
        return given
    return reference["own_doi"]


def next_published_stage(reference: dict, latest: dict, refresh_days: float) -> str | None:
    """
    The next source to ask about a preprint's published version, or ``None`` when it is settled.

    ``arxiv`` first, for the DOI the authors gave. Then ``crossref``, for the
    record of that DOI, or of the reference's own DOI, which is where the
    journal, volume, issue and pages come from.
    """
    arxiv = latest.get((reference["tag"], "arxiv", "arxiv_id", reference["arxiv"]))
    if not journal.is_fresh(arxiv, refresh_days):
        return ARXIV_STAGE
    doi = published_doi(reference, latest)
    if doi and not journal.is_fresh(latest.get((reference["tag"], "crossref", "doi", doi)), refresh_days):
        return CROSSREF_STAGE
    return None


@dataclass
class PublishedPlan:
    """
    What a search for published versions over a subset would do.

    Attributes
    ----------
    subset : int
        References in the subset.
    not_arxiv : int
        References with no arXiv identifier.
    has_journal : int
        arXiv references that already have a journal; taken as published.
    fresh : int
        Preprints asked about within the refresh period; skipped.
    references : dict
        ``tag -> {tag, title, author, year, arxiv, own_doi}`` still to ask about.
    stages : dict
        ``tag -> stage`` each of them is at.
    refresh_days : float
        The refresh period the plan was made with.
    """

    subset: int = 0
    not_arxiv: int = 0
    has_journal: int = 0
    fresh: int = 0
    references: dict[str, dict] = field(default_factory=dict)
    stages: dict[str, str] = field(default_factory=dict)
    refresh_days: float = 30.0

    @property
    def tags(self) -> int:
        """The number of preprints to ask about."""
        return len(self.references)

    def at(self, stage: str) -> list[str]:
        """The tags at a stage."""
        return [tag for tag, s in self.stages.items() if s == stage]

    @property
    def arxiv_requests(self) -> int:
        """Requests to arXiv, 100 identifiers each."""
        return -(-len({self.references[tag]["arxiv"] for tag in self.at(ARXIV_STAGE)}) // sources.ARXIV_BATCH)

    @property
    def requests(self) -> int:
        """The fewest requests: no preprint asked of arXiv has a published version."""
        return self.arxiv_requests + len(self.at(CROSSREF_STAGE))

    @property
    def requests_most(self) -> int:
        """The most requests: every preprint asked of arXiv has a DOI to look up at Crossref."""
        return self.requests + len(self.at(ARXIV_STAGE))

    def seconds(self, mode: str, requests: int | None = None) -> float:
        """Estimate the duration in a mode: three seconds an arXiv request, the mode's pace for the rest."""
        pace = egress.MODES[mode]
        singles = (self.requests if requests is None else requests) - self.arxiv_requests
        paced = self.arxiv_requests * egress.HOST_PACE[egress.ARXIV].search_interval + singles * pace.single_interval
        return max(paced, singles * ASSUMED_LATENCY_SECONDS / pace.concurrency)


def plan_published(library_path: Path, rows: pd.DataFrame, refresh_days: float) -> PublishedPlan:
    """
    Work out what a search for published versions over ``rows`` would ask.

    A reference is a preprint here when it has an arXiv identifier and no
    journal.

    Parameters
    ----------
    library_path : Path
        The library folder, for the journal.
    rows : pandas.DataFrame
        The references of the subset.
    refresh_days : float
        An answer newer than this is fresh and is not asked for again.

    Returns
    -------
    PublishedPlan
    """
    plan = PublishedPlan(subset=len(rows), refresh_days=refresh_days)
    latest = journal.latest_observations(library_path)
    for row in rows.to_dict("records"):
        reference = preprint_reference(row)
        if reference is None:
            if reference_arxiv_id(row):
                plan.has_journal += 1
            else:
                plan.not_arxiv += 1
            continue
        stage = next_published_stage(reference, latest, refresh_days)
        if stage is None:
            plan.fresh += 1
        else:
            plan.references[row["tag"]] = reference
            plan.stages[row["tag"]] = stage
    return plan


def preprint_reference(row: dict) -> dict | None:
    """
    Describe a reference as a preprint, or return ``None`` if it is not one.

    Returns
    -------
    dict or None
        ``tag``, ``title``, ``author``, ``year``, ``type``, ``arxiv`` (the
        identifier) and ``own_doi`` (the reference's DOI in compared form when
        it is a usable DOI that is not arXiv's own, else ``None``), for a
        reference with an arXiv identifier and no journal.
    """
    identifier = reference_arxiv_id(row)
    journal_name = cell_value(row.get("journal"))
    if not identifier or (journal_name and journal_name.strip()):
        return None
    doi = cell_value(row.get("doi"))
    usable = bool(doi and doi.strip()) and classify_stored_doi(doi) != ATTENTION and not is_arxiv_doi(doi)
    return {"tag": row["tag"], "title": cell_value(row.get("title")), "author": cell_value(row.get("author")),
            "year": cell_value(row.get("year")), "type": cell_value(row.get("type")),
            "arxiv": identifier, "own_doi": doi_key(doi) if usable else None}


class PublishedRun(Run):
    """
    A search for the published versions of preprints.

    arXiv is asked first, 100 identifiers a request, for the DOI the authors
    gave. Crossref is then asked for the record of each such DOI, one a
    request. Only arXiv identifiers and DOIs are sent.

    Parameters
    ----------
    library_path : Path
        The library folder.
    plan : PublishedPlan
        From :func:`plan_published`.
    mode : str
        ``egress.SPEEDY`` or ``egress.POLITE``; arXiv is paced the same in both.
    client, on_progress
        As for :class:`Run`.
    """

    action = "published"

    def __init__(self, library_path: Path, plan: PublishedPlan, mode: str, *,
                 client: egress.Egress | None = None, on_progress=None):
        self.plan = plan
        self.latest = dict(journal.latest_observations(library_path))
        self._latest_lock = threading.Lock()
        super().__init__(library_path, mode, total=plan.tags, requests_planned=plan.requests,
                         client=client, on_progress=on_progress)

    def _answered(self, observation: dict) -> None:
        stamped = {"at": journal.now_utc(), **observation}
        with self._latest_lock:
            self.latest[(stamped["tag"], stamped["source"], stamped["query_kind"], stamped["query"])] = stamped

    def _stage(self, tag: str) -> str | None:
        with self._latest_lock:
            return next_published_stage(self.plan.references[tag], self.latest, self.plan.refresh_days)

    def _arxiv_jobs(self) -> list[tuple]:
        """Jobs asking arXiv about the identifiers at the arXiv stage, 100 a request."""
        work: dict[str, list[str]] = {}
        for tag in self.plan.at(ARXIV_STAGE):
            work.setdefault(self.plan.references[tag]["arxiv"], []).append(tag)
        identifiers = list(work)
        batches = [identifiers[i:i + sources.ARXIV_BATCH] for i in range(0, len(identifiers), sources.ARXIV_BATCH)]

        def record(batch, found, error=""):
            observations, tally = [], {"found": 0, "not_found": 0, "errors": 0, "done": 0}
            for identifier in batch:
                for tag in work[identifier]:
                    base = {"run_id": self.run_id, "tag": tag, "source": "arxiv", "query_kind": "arxiv_id", "query": identifier}
                    if error:
                        observations.append({**base, "status": journal.ERROR, "error": error})
                        tally["errors"] += 1
                        tally["done"] += 1
                        continue
                    answer = found.get(identifier)
                    observations.append({**base, "status": journal.OK, **answer} if answer
                                        else {**base, "status": journal.NOT_FOUND})
                    self._answered(observations[-1])
                    tally["found" if answer else "not_found"] += 1
                    tally["done"] += 1 if self._stage(tag) is None else 0
            journal.append_lines(self.library_path, journal.OBSERVATIONS, observations)
            self.tally(requests=1, **tally)

        return [(lambda batch=batch: record(batch, sources.arxiv_records(self.client, batch)),
                 lambda message, batch=batch: record(batch, {}, error=message)) for batch in batches]

    def phases(self):
        yield "arXiv", self._arxiv_jobs()
        work: dict[str, list[str]] = {}
        for tag, reference in self.plan.references.items():
            if self._stage(tag) == CROSSREF_STAGE:
                with self._latest_lock:
                    doi = published_doi(reference, self.latest)
                work.setdefault(doi, []).append(tag)
        yield "Crossref", self._single_jobs("crossref", work, lambda key: sources.crossref_work(self.client, key))


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
