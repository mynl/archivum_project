"""Hashing multiple files using a thread pool (from file_database_project)."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path


def blake2b_hash(path: Path, block_size: int = 65536) -> str:
    """Compute blake2b hash of a file."""
    h = hashlib.blake2b()
    with path.open("rb") as f:
        while chunk := f.read(block_size):
            h.update(chunk)
    return h.hexdigest()


def hash_many(paths: list[Path], workers: int) -> dict:
    """Multi-threaded hashing of list of files."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(blake2b_hash, p): p for p in paths}
        return {futures[f]: f.result() for f in futures if f.exception() is None}
