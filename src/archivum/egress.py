"""
The one place archivum sends a request to a public source.

Every outbound call for reference data goes through :class:`Egress`, which
allows only the hosts in :data:`ALLOWED_HOSTS`, over https, and paces them.
Nothing of the user's own goes out beyond a title, an author surname, a year
or an identifier, which is what a lookup of a published work needs.

Two callers, two ways of failing. An interactive lookup (the ``crossref``
functions, during ingest) uses :func:`default` and ``retry=False``: one
attempt, a short timeout, and the answer or the error at once. A DOI audit run
makes its own :class:`Egress` and lets it retry with backoff, stop a host that
keeps failing, and stop on a low budget, for the length of that run.

The Ingest page's download of a URL the user types is not routed here: its
host cannot be listed in advance.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from . import GLOBAL_CONFIG
from . import __version__

logger = logging.getLogger(__name__)

OPENALEX, CROSSREF, DOI_ORG, ARXIV = "api.openalex.org", "api.crossref.org", "doi.org", "export.arxiv.org"
SEMANTIC_SCHOLAR = "api.semanticscholar.org"
ALLOWED_HOSTS = frozenset({OPENALEX, CROSSREF, DOI_ORG, ARXIV, SEMANTIC_SCHOLAR})

SPEEDY, POLITE = "speedy", "polite"
SINGLE, SEARCH = "single", "search"

# A run needing this many requests or fewer defaults to speedy.
SPEEDY_REQUEST_LIMIT = 500


@dataclass(frozen=True)
class Pace:
    """
    How fast one host may be asked.

    Attributes
    ----------
    single_interval, search_interval : float
        The least number of seconds between the starts of two requests, for a
        lookup of one record and for a search.
    concurrency : int
        The most requests in flight at once.
    """

    single_interval: float
    search_interval: float
    concurrency: int

    def interval(self, kind: str) -> float:
        """Return the interval for a request ``kind``."""
        return self.search_interval if kind == SEARCH else self.single_interval


# Speedy stays under the published limits found 2026-11-17: Crossref polite
# pool 10 single records and 3 searches a second, 3 at once; OpenAlex 30 a
# second with a key.
MODES = {
    SPEEDY: Pace(single_interval=0.2, search_interval=0.5, concurrency=3),
    POLITE: Pace(single_interval=1.0, search_interval=1.0, concurrency=1),
}
# arXiv's terms of use: one request every three seconds on one connection,
# whatever the mode.
HOST_PACE = {ARXIV: Pace(single_interval=3.0, search_interval=3.0, concurrency=1)}

DEFAULT_TIMEOUT = 30.0
BACKOFF_FIRST_SECONDS = 30.0
BACKOFF_CAP_SECONDS = 3600.0
FAILURES_TO_STOP_HOST = 5
# stop asking OpenAlex when this fraction of the day's budget is left
BUDGET_FLOOR = 0.02


class EgressRefused(ValueError):
    """The URL is not https, or its host is not in :data:`ALLOWED_HOSTS`."""


class HostStopped(RuntimeError):
    """The host failed too often, or its budget ran low; it is not asked again in this run."""


class EgressFailed(RuntimeError):
    """A request made with ``retry=False`` could not be sent or got no answer."""


class Stopped(RuntimeError):
    """The run was told to stop while a request was waiting."""


def choose_mode(request_count: int) -> str:
    """Return the default mode for a run: speedy up to :data:`SPEEDY_REQUEST_LIMIT` requests, polite above."""
    return SPEEDY if request_count <= SPEEDY_REQUEST_LIMIT else POLITE


def _requests_transport(method: str, url: str, *, params, headers, timeout, json=None):
    """
    Send one request with ``requests``; the only call to it in the package besides Ingest's download.

    A redirect is returned, never followed: following one would send a request
    to a host the allow-list has not seen. ``doi.org`` answers an existing DOI
    with a redirect to the publisher, and that answer is all that is wanted.
    """
    return requests.request(method, url, params=params, headers=headers, timeout=timeout, json=json,
                            allow_redirects=False)


@dataclass
class _HostState:
    """Pacing and failure state of one host within one :class:`Egress`."""

    pace: Pace
    lock: threading.Lock = field(default_factory=threading.Lock)
    slots: threading.Semaphore = field(init=False)
    next_start: float = 0.0
    requests: int = 0
    failures: int = 0
    stopped: str = ""
    budget_limit: float | None = None
    budget_remaining: float | None = None

    def __post_init__(self):
        self.slots = threading.Semaphore(self.pace.concurrency)


class Egress:
    """
    Paced, allow-listed HTTP for one run, or for interactive lookups.

    Parameters
    ----------
    mode : str, optional
        ``speedy`` or ``polite``; see :data:`MODES`.
    contact_email : str, optional
        Sent in the ``User-Agent``, and as ``mailto`` to Crossref and OpenAlex,
        which is how both identify a polite caller. Defaults to
        ``contact_email`` in the global config.
    transport : callable, optional
        ``transport(method, url, params=, headers=, timeout=)`` returning an
        object with ``status_code``, ``headers`` and ``json()``. Tests pass a
        fake; the default sends with ``requests``.
    clock, sleep : callable, optional
        Time source and wait, replaced in tests.
    stop : threading.Event, optional
        Set to make every wait end with :class:`Stopped`.

    Notes
    -----
    Rate is bounded by a mechanism, not a delay. For each host a lock hands
    out start times at least one interval apart, and a semaphore bounds the
    requests in flight, so :math:`n` threads sharing one ``Egress`` still send
    at most one request per interval. Every request has a timeout.

    With ``retry=True`` a 429, a 5xx or a failed connection waits and tries
    again: ``Retry-After`` when the host sends it, otherwise
    :data:`BACKOFF_FIRST_SECONDS` doubling to :data:`BACKOFF_CAP_SECONDS`.
    After :data:`FAILURES_TO_STOP_HOST` failures in a row the host is stopped
    for the life of this object. OpenAlex reports the day's budget in
    ``X-RateLimit-Limit`` and ``X-RateLimit-Remaining``; when less than
    :data:`BUDGET_FLOOR` of it is left the host is stopped the same way.
    """

    def __init__(self, mode: str = SPEEDY, *, contact_email: str | None = None,
                 transport: Callable | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] | None = None, stop: threading.Event | None = None):
        if mode not in MODES:
            raise ValueError(f"Unknown egress mode {mode!r}")
        self.mode = mode
        self.contact_email = GLOBAL_CONFIG.get("contact_email", "") if contact_email is None else contact_email
        self.transport = transport or _requests_transport
        self.clock = clock
        self.stop = stop or threading.Event()
        self._sleep = sleep or self._wait
        self._hosts: dict[str, _HostState] = {}
        self._hosts_lock = threading.Lock()

    def _wait(self, seconds: float) -> None:
        """Wait, ending early with :class:`Stopped` if the run is stopped."""
        if self.stop.wait(seconds):
            raise Stopped("stopped while waiting")

    def _host(self, host: str) -> _HostState:
        with self._hosts_lock:
            if host not in self._hosts:
                self._hosts[host] = _HostState(HOST_PACE.get(host, MODES[self.mode]))
            return self._hosts[host]

    @staticmethod
    def check_url(url: str) -> str:
        """
        Return the host of ``url`` if archivum may ask it.

        Raises
        ------
        EgressRefused
            If the scheme is not https or the host is not allowed.
        """
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in ALLOWED_HOSTS:
            raise EgressRefused(f"Outbound request refused: {parts.scheme}://{host} is not an allowed source.")
        return host

    def _headers(self, host: str, headers: dict | None) -> dict:
        """The headers of a request: the ``User-Agent``, and the Semantic Scholar key from the environment for that host."""
        agent = f"archivum/{__version__}"
        if self.contact_email:
            agent += f" (mailto:{self.contact_email})"
        key = {}
        if host == SEMANTIC_SCHOLAR and os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
            key["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
        return {"User-Agent": agent, **key, **(headers or {})}

    def _params(self, host: str, params: dict | None) -> dict | None:
        """Add the caller's identity where the host asks for it: ``mailto``, and the OpenAlex key from the environment."""
        extra = {}
        if host in (CROSSREF, OPENALEX) and self.contact_email:
            extra["mailto"] = self.contact_email
        if host == OPENALEX and os.environ.get("OPENALEX_API_KEY"):
            extra["api_key"] = os.environ["OPENALEX_API_KEY"]
        if not extra:
            return params
        return {**(params or {}), **extra}

    def request(self, method: str, url: str, *, params: dict | None = None, headers: dict | None = None,
                json: Any = None, kind: str = SINGLE, retry: bool = True, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """
        Send one request to an allowed host, paced.

        Parameters
        ----------
        method : str
            ``GET`` or ``POST``.
        url : str
            An https URL on an allowed host.
        params, headers : dict, optional
            Query parameters and extra headers.
        json : optional
            A body to send as JSON, for a POST.
        kind : str, optional
            ``single`` for one record, ``search`` for a search; sets the pace.
        retry : bool, optional
            Whether to wait and retry on 429, 5xx and connection failures. An
            interactive caller passes False and gets the response or the
            exception at once.
        timeout : float, optional
            Seconds allowed for the request.

        Returns
        -------
        response
            Whatever the transport returns. A 404 is a response, not an error.

        Raises
        ------
        EgressRefused
            For a URL that is not allowed; nothing is sent.
        HostStopped
            If the host has been stopped in this run.
        Stopped
            If the run was stopped during a wait.
        EgressFailed
            With ``retry=False``, when the request could not be sent or timed
            out. A caller therefore needs nothing from ``requests``.
        """
        host = self.check_url(url)
        state = self._host(host)
        params, headers = self._params(host, params), self._headers(host, headers)
        # passed on only when there is one, so a transport without bodies need not know of them
        body = {} if json is None else {"json": json}
        while True:
            if state.stopped:
                raise HostStopped(f"{host} is stopped for this run: {state.stopped}")
            if self.stop.is_set():
                raise Stopped("stopped")
            with state.slots:
                self._take_turn(state, kind)
                state.requests += 1
                try:
                    response = self.transport(method, url, params=params, headers=headers, timeout=timeout, **body)
                    error = None
                except (requests.RequestException, OSError) as e:
                    response, error = None, e
            failed = error is not None or response.status_code == 429 or response.status_code >= 500
            if not failed:
                state.failures = 0
                self._note_budget(host, state, response)
                return response
            if not retry:
                if error is not None:
                    raise EgressFailed(f"{host}: {type(error).__name__}: {error}") from error
                return response
            self._after_failure(host, state, response, error)

    def _take_turn(self, state: _HostState, kind: str) -> None:
        """Wait for this request's start time; start times are handed out one interval apart."""
        with state.lock:
            now = self.clock()
            start = max(now, state.next_start)
            state.next_start = start + state.pace.interval(kind)
        if start > now:
            self._sleep(start - now)

    def _after_failure(self, host: str, state: _HostState, response, error) -> None:
        """Count a failure; stop the host on the fifth in a row, otherwise wait before the retry."""
        with state.lock:
            state.failures += 1
            failures = state.failures
            what = f"HTTP {response.status_code}" if response is not None else f"{type(error).__name__}: {error}"
            if failures >= FAILURES_TO_STOP_HOST:
                state.stopped = f"{failures} failures in a row, last {what}"
        if state.stopped:
            logger.error("Egress stopped %s: %s", host, state.stopped)
            raise HostStopped(f"{host} is stopped for this run: {state.stopped}")
        wait = _retry_after(response) if response is not None else None
        if wait is None:
            wait = min(BACKOFF_FIRST_SECONDS * 2 ** (failures - 1), BACKOFF_CAP_SECONDS)
        logger.warning("Egress %s failed (%s); waiting %.0f s", host, what, wait)
        self._sleep(wait)

    def _note_budget(self, host: str, state: _HostState, response) -> None:
        """Record the budget headers a host sends, and stop the host when little is left."""
        try:
            limit = float(response.headers.get("X-RateLimit-Limit"))
            remaining = float(response.headers.get("X-RateLimit-Remaining"))
        except (TypeError, ValueError, AttributeError):
            return
        state.budget_limit, state.budget_remaining = limit, remaining
        if host == OPENALEX and limit > 0 and remaining <= BUDGET_FLOOR * limit and not state.stopped:
            state.stopped = f"budget low: {remaining:g} of {limit:g} left today"
            logger.warning("Egress stopped %s: %s", host, state.stopped)

    def get(self, url: str, **kwargs) -> Any:
        """Send a GET; see :meth:`request`."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
        """Send a POST; see :meth:`request`."""
        return self.request("POST", url, **kwargs)

    def summary(self) -> dict:
        """
        What this object has sent, by host, for a run summary.

        Returns
        -------
        dict
            ``host -> {requests, stopped, budget_limit, budget_remaining}``.
        """
        return {
            host: {"requests": s.requests, "stopped": s.stopped,
                   "budget_limit": s.budget_limit, "budget_remaining": s.budget_remaining}
            for host, s in self._hosts.items()
        }


def _retry_after(response) -> float | None:
    """Return the seconds a ``Retry-After`` header asks for, whether a number or a date; ``None`` without one."""
    value = getattr(response, "headers", {}).get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())


_DEFAULT: Egress | None = None
_DEFAULT_LOCK = threading.Lock()


def default() -> Egress:
    """Return the process-wide :class:`Egress` for interactive lookups, in speedy mode."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = Egress(SPEEDY)
        return _DEFAULT
