import logging
import time
from datetime import datetime

import pandas as pd


logger = logging.getLogger(__name__)


_ANALYTICS_CACHE = {
    "lib_id": None,
    "last_sync": None,
    "author_counts": None,
    "history": None,
    "insights": None,
    "audit": None,
}

_META_CACHE = {
    "lib_name": None,
    "mtime": 0,
    "data": {},
}

_RG_CACHE = {
    "lib_name": None,
    "last_sync": None,
    "date": None,
    "data": {},
    "html": {},
    "stats": {},
}


def get_cached_data(lib, key, calculator):
    """Retrieve expensive analytics from cache or recalculate after metadata changes."""
    global _ANALYTICS_CACHE
    lib_id = id(lib)
    feather_path = lib.config_path / "ref.feather"
    last_sync = feather_path.stat().st_mtime if feather_path.exists() else 0

    if _ANALYTICS_CACHE["lib_id"] != lib_id or _ANALYTICS_CACHE["last_sync"] != last_sync:
        logger.info("Invalidating cache for %s (last_sync: %s)", lib.name, last_sync)
        _ANALYTICS_CACHE = {
            "lib_id": lib_id,
            "last_sync": last_sync,
            "author_counts": None,
            "history": None,
            "insights": None,
            "audit": None,
        }

    if _ANALYTICS_CACHE.get(key) is None:
        start_time = time.time()
        _ANALYTICS_CACHE[key] = calculator()
        logger.info("Calculated %s in %.2fs", key, time.time() - start_time)

    return _ANALYTICS_CACHE[key]


def invalidate_cached_data(key):
    if key in _ANALYTICS_CACHE:
        _ANALYTICS_CACHE[key] = None


def get_hash_meta_cache(lib):
    global _META_CACHE
    feather_path = lib.config_path / "ref.feather"
    mtime = feather_path.stat().st_mtime if feather_path.exists() else 0

    if _META_CACHE["lib_name"] != lib.name or _META_CACHE["mtime"] != mtime:
        logger.info("Rebuilding metadata cache for %s", lib.name)
        hash_prefix_to_meta = {}
        if not lib.ref_doc_df.empty:
            latest_links = (
                lib.ref_doc_df.sort_values(["hash", "version"], ascending=[True, False])
                .drop_duplicates("hash")
            )
            meta_df = latest_links.merge(lib.doc_df, on=["hash", "version"], how="left")
            meta_df = meta_df.merge(lib.ref_df, on="tag", how="left")

            for _, row in meta_df.iterrows():
                prefix = str(row.hash)[:10]
                hash_prefix_to_meta[prefix] = {
                    "tag": row.tag,
                    "title": str(row.get("title", "")).replace("{", "").replace("}", ""),
                    "authors": str(row.get("author", "")),
                    "year": str(row.get("year", "9999")).split(".")[0],
                    "publisher": str(row.get("publisher", "")) if pd.notna(row.get("publisher")) else "",
                    "size": int(row.get("size", 0)) if pd.notna(row.get("size")) else 0,
                }
        _META_CACHE["lib_name"] = lib.name
        _META_CACHE["mtime"] = mtime
        _META_CACHE["data"] = hash_prefix_to_meta

    return _META_CACHE["data"]


def get_rg_cache_item(lib, key, subkey="html"):
    global _RG_CACHE
    lib_id = lib.name
    feather_path = lib.config_path / "ref.feather"
    last_sync = feather_path.stat().st_mtime if feather_path.exists() else 0
    today = datetime.now().date()

    if (
        _RG_CACHE["lib_name"] != lib_id
        or _RG_CACHE["last_sync"] != last_sync
        or _RG_CACHE["date"] != today
    ):
        logger.info("Invalidating RG cache for %s", lib_id)
        _RG_CACHE = {
            "lib_name": lib_id,
            "last_sync": last_sync,
            "date": today,
            "data": {},
            "html": {},
            "stats": {},
        }

    return _RG_CACHE[subkey].get(key)


def set_rg_cache_item(key, value, subkey="html"):
    if len(_RG_CACHE[subkey]) > 100:
        _RG_CACHE[subkey].pop(next(iter(_RG_CACHE[subkey])))
    _RG_CACHE[subkey][key] = value
