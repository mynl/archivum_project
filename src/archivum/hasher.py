"""Hashing multiple files using a thread pool (from file_database_project)."""

from concurrent.futures import ThreadPoolExecutor
# from functools import lru_cache
import hashlib
from pathlib import Path
import blake3

_MEMORY_CACHE = {}


def blake2b_hash(file_path: Path, block_size: int = 65536) -> str:
    """Compute blake2b hash of a file."""
    if file_path in _MEMORY_CACHE:
        return _MEMORY_CACHE[file_path]

    h = hashlib.blake2b()
    # ensure it is a Path
    path = Path(file_path)
    with path.open("rb") as f:
        while chunk := f.read(block_size):
            h.update(chunk)
    res = h.hexdigest().upper()
    _MEMORY_CACHE[file_path] = res
    return res


def hash_many(paths: list[Path], workers: int) -> dict:
    """Multi-threaded hashing of list of files."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(blake2b_hash, p): p for p in paths}
        return {futures[f]: f.result() for f in futures if f.exception() is None}


def qhash(s: str) -> str:
    """Quick hash of a string."""
    h = hashlib.md5()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def blake3b_hash(file_path) -> str:
    if file_path in _MEMORY_CACHE:
        return _MEMORY_CACHE[file_path]

    b3 = blake3.blake3()
    # Blake3 has internal multithreading; memory mapping is efficient
    b3.update_mmap(str(file_path))
    res = b3.hexdigest().upper()
    _MEMORY_CACHE[file_path] = res
    return res


def hash_many3_basic(paths: list[Path], workers: int) -> dict:
    """Multi-threaded hashing of list of files."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(blake3b_hash, p): p for p in paths}
        return {futures[f]: f.result() for f in futures if f.exception() is None}


def hash_many3(paths: list[Path], workers: int) -> dict:
    """Multi-threaded hashing of list of files with roll your own cache."""
    results = {}
    to_submit = []

    # 1. Check cache immediately to avoid thread overhead
    for p in paths:
        if p in _MEMORY_CACHE:
            results[p] = _MEMORY_CACHE[p]
        else:
            to_submit.append(p)

    # 2. Only thread the misses
    if to_submit:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(blake3b_hash, p): p for p in to_submit}
            for f in futures:
                if f.exception() is None:
                    results[futures[f]] = f.result()

    return results
