"""File-based cache for cell measurement data.

Provides a local disk cache for steps and time-series data fetched from the
Ionworks API.  DataFrames are stored as parquet files, raw file downloads
are stored as individual files in a ``files/`` subdirectory, and ``None``
values are represented by ``.null`` marker files. The cache is keyed by
measurement ID and supports configurable TTL, custom directory, and
enable/disable toggling.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import time

import pandas as pd
import polars as pl

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
_CACHE_CONFIG: dict = {
    "enabled": True,
    "directory": Path.home() / ".ionworksdata_cache",
    "ttl_seconds": 3600,  # 1 hour default TTL
}


def set_cache_enabled(enabled: bool) -> None:
    """Enable or disable the local measurement cache."""
    _CACHE_CONFIG["enabled"] = enabled


def get_cache_enabled() -> bool:
    """Return whether the local measurement cache is currently enabled."""
    return _CACHE_CONFIG["enabled"]


def set_cache_directory(directory: str | Path) -> None:
    """Set the directory used for cached measurement files."""
    _CACHE_CONFIG["directory"] = Path(directory)


def get_cache_directory() -> Path:
    """Return the current cache directory."""
    return _CACHE_CONFIG["directory"]


def set_cache_ttl(ttl_seconds: int | None) -> None:
    """Set the cache time-to-live in seconds.

    Parameters
    ----------
    ttl_seconds : int | None
        Seconds before cached data is considered stale. Set to ``None`` to
        disable TTL (cache never expires). Default is 3600 (1 hour).
    """
    _CACHE_CONFIG["ttl_seconds"] = ttl_seconds


def get_cache_ttl() -> int | None:
    """Return the current cache TTL in seconds, or ``None`` if disabled."""
    return _CACHE_CONFIG["ttl_seconds"]


def clear_cache() -> int:
    """Delete all cached measurement entries.

    Returns
    -------
    int
        Number of cache entries deleted.
    """
    cache_dir = _CACHE_CONFIG["directory"]
    if not cache_dir.exists():
        return 0

    count = 0
    # Remove parquet-based cache directories
    for entry in cache_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
            count += 1
    # Also clean up any legacy .pkl files
    for pkl_file in cache_dir.glob("*.pkl"):
        pkl_file.unlink()
        count += 1
    return count


# -------------------------------------------------------------------------
# Internal helpers
# -------------------------------------------------------------------------
def _get_cache_dir(measurement_id: str) -> Path:
    """Return the cache directory path for *measurement_id*."""
    hash_key = hashlib.md5(measurement_id.encode(), usedforsecurity=False).hexdigest()[
        :16
    ]
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in measurement_id)
    return _CACHE_CONFIG["directory"] / f"{safe_id}_{hash_key}"


def _load_from_cache(measurement_id: str) -> dict | None:
    """Load measurement data from cache if available and not expired.

    The TTL is measured per file, so a partial update (e.g. caching
    ``time_series`` into a directory that already holds an expired
    ``steps.parquet``) does not resurrect the expired sibling: only
    fresh artifacts are returned. Loading never deletes files (except
    on a corrupted entry), so a concurrent ``_save_to_cache`` cannot
    lose its write; expired files are simply skipped and overwritten
    by the next fetch of that artifact.

    Returns
    -------
    dict | None
        Cached data dict with ``"time_series"``, ``"steps"``, and/or
        ``"cycles"`` polars DataFrames (or ``None`` per key), or ``None``
        if not cached or expired.
    """
    if not _CACHE_CONFIG["enabled"]:
        return None

    cache_dir = _get_cache_dir(measurement_id)
    if not cache_dir.exists():
        return None

    try:
        result = _read_cache_entry(cache_dir)
        return result or None
    except Exception:
        shutil.rmtree(cache_dir, ignore_errors=True)
        return None


def _is_fresh(path: Path, now: float) -> bool:
    """Return whether *path* is within the configured TTL."""
    ttl_seconds = _CACHE_CONFIG["ttl_seconds"]
    if ttl_seconds is None:
        return True
    try:
        return now - path.stat().st_mtime <= ttl_seconds
    except OSError:
        return False


def _read_cache_entry(cache_dir: Path) -> dict:
    """Read unexpired cached files from a cache entry directory.

    Files older than the configured TTL are skipped (not deleted).

    Returns
    -------
    dict
        Reconstructed data dict. ``.parquet`` files are loaded as polars
        DataFrames, ``.null`` marker files produce ``None`` values, and a
        ``files/`` subdirectory is loaded as a ``dict[str, bytes]`` mapping
        of filename to raw bytes. Keys are derived from the file stems.
    """
    now = time.time()
    result: dict = {}
    for f in cache_dir.glob("*.parquet"):
        if _is_fresh(f, now):
            result[f.stem] = pl.read_parquet(f)
    for f in cache_dir.glob("*.null"):
        if _is_fresh(f, now):
            result[f.stem] = None
    # Load raw file downloads from the files/ subdirectory. The map is
    # served as a single dict, so expire it atomically: any stale (or
    # missing) file makes the whole map a miss, never a partial hit.
    files_dir = cache_dir / "files"
    if files_dir.is_dir():
        files = [f for f in files_dir.iterdir() if f.is_file()]
        if files and all(_is_fresh(f, now) for f in files):
            result["files"] = {f.name: f.read_bytes() for f in files}
    return result


def _save_to_cache(measurement_id: str, data: dict) -> None:
    """Save measurement data to cache.

    DataFrames are stored as parquet files. A ``"files"`` key containing a
    ``dict[str, bytes]`` mapping is written as individual files in a
    ``files/`` subdirectory. ``None`` values are represented by ``.null``
    marker files. New entries are merged with any existing files in the
    cache directory.
    """
    if not _CACHE_CONFIG["enabled"]:
        return

    cache_dir = _get_cache_dir(measurement_id)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for key, value in data.items():
            if isinstance(value, (pl.DataFrame, pd.DataFrame)):
                df = value if isinstance(value, pl.DataFrame) else pl.from_pandas(value)
                (cache_dir / f"{key}.null").unlink(missing_ok=True)
                df.write_parquet(cache_dir / f"{key}.parquet")
            elif key == "files" and isinstance(value, dict):
                # Store each downloaded file individually
                files_dir = cache_dir / "files"
                files_dir.mkdir(exist_ok=True)
                for filename, content in value.items():
                    (files_dir / filename).write_bytes(content)
            elif value is None:
                (cache_dir / f"{key}.parquet").unlink(missing_ok=True)
                (cache_dir / f"{key}.null").touch()
    except Exception:
        pass
