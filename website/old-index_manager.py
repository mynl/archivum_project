"""Manage index loading and updating."""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict

import pandas as pd


from . blitz_adapter import BlitzAdapter, BlitzSnapshot
from . eventbus import EventBus
from . util import AtomicRef


logger = logging.getLogger(__name__)


@dataclass
class IndexState:    # noqa
    name: str
    adapter: BlitzAdapter
    snapshot_ref: AtomicRef  # AtomicRef[BlitzSnapshot]
    version_bus: EventBus
    refresh_seconds: int


class IndexManager:   # noqa

    def __init__(self, mapping: Dict[str, BlitzAdapter],
        refresh_seconds: int = 300):
        """Create IndexManager from directory path->Adapter."""
        self._states: Dict[str, IndexState] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._stop = threading.Event()

        for name, adapter in mapping.items():
            logger.debug('IndexManager adding adapter %s to IndexManager, refresh period %s seconds',
                         name, refresh_seconds)
            snap = adapter.load_snapshot()
            state = IndexState(
                name=name,
                adapter=adapter,
                snapshot_ref=AtomicRef(snap),
                version_bus=EventBus(),
                refresh_seconds=refresh_seconds,
            )
            self._states[name] = state

    def start(self) -> None:
        """Start all indexes."""
        for name, state in self._states.items():
            logger.debug('IndexManager starting %s', name)
            t = threading.Thread(target=self._worker, args=(state,), name=f"refresh-{name}", daemon=True)
            t.start()
            self._threads[name] = t

    def stop(self) -> None:
        """Stop all indexes."""
        logger.debug('IndexManager stopping all (n=%s) threads', len(self._threads))
        self._stop.set()
        for t in self._threads.values():
            logger.debug('IndexManager stopping %s', t.name)
            t.join(timeout=2)

    def _worker(self, state: IndexState) -> None:
        logger.info("IndexManager refresh worker started for index: %s", state.name)
        while not self._stop.is_set():
            try:
                current: BlitzSnapshot = state.snapshot_ref.get()
                new_snap = state.adapter.refresh_incremental(current)
                if new_snap.version != current.version:
                    state.snapshot_ref.swap(new_snap)
                    # Notify listeners to refetch.
                    state.version_bus.publish(str(new_snap.version))
                    logger.info("Index %s updated to version %s", state.name, new_snap.version)
            except Exception as e:  # noqa: BLE001
                logger.exception("Refresh error for %s: %s", state.name, e)
            # Sleep in small chunks so stop() is responsive.
            for _ in range(state.refresh_seconds):
                if self._stop.is_set():
                    break
                time.sleep(1)

    # ---- Reader helpers ----
    def get_snapshot(self, name: str) -> BlitzSnapshot:
        logger.debug('IndexManager getting snapshot for %s', name)
        return self._states[name].snapshot_ref.get()

    def get_bus(self, name: str) -> EventBus:
        logger.debug('IndexManager getting event bus for %s', name)
        return self._states[name].version_bus

    def list_indexes(self) -> list[str]:
        logger.debug('IndexManager getting list of indexes')
        return sorted(self._states.keys())
