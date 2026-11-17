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
from .doi import ATTENTION, VERIFIED, YEAR_DIFFERS, classify_stored_doi, doi_key, match

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

    def phases(self):
        yield "OpenAlex", self._openalex_jobs(self.plan.work)


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

    def phases(self):
        yield "OpenAlex", self._openalex_jobs(
            self.plan.keys_at(OPENALEX_STAGE), settled=lambda tag, record: self._stage(tag) is None)
        yield "Crossref", self._single_jobs(
            "crossref", self._keys_at(CROSSREF_STAGE), lambda key: sources.crossref_work(self.client, key))
        yield "doi.org", self._single_jobs(
            "doi.org", self._keys_at(DOI_ORG_STAGE),
            lambda key: {"exists": True} if sources.doi_exists(self.client, key) else None)


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
