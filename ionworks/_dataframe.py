"""Shared DataFrame <-> parquet helpers for SDK clients.

Small, dependency-light utilities used by clients that upload/download tabular
data as parquet (e.g. :mod:`ionworks.analysis`). Kept here so the conversion
logic lives in one place rather than being copied per client.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import io
import os
import tempfile
from typing import IO

import pandas as pd
import polars as pl

from .validators import DataFrame, get_dataframe_backend

#: Uncompressed in-memory frame size at or above which the parquet is written
#: to a temporary file rather than held in RAM. Compared against polars'
#: ``estimated_size()``, which measures the *uncompressed* frame, so it is an
#: upper bound on the parquet that comes out -- the check errs toward keeping
#: the in-memory path, never toward spilling a frame that would have fitted.
PARQUET_SPILL_THRESHOLD_BYTES: int = 512 * 1024 * 1024


def to_polars(df: DataFrame | dict) -> pl.DataFrame:
    """Convert a pandas DataFrame, polars DataFrame, or dict to polars.

    Parameters
    ----------
    df : DataFrame | dict
        Input data as a polars DataFrame, pandas DataFrame, or a
        column-name -> values dict.

    Returns
    -------
    pl.DataFrame
        The data as a polars DataFrame.

    Raises
    ------
    TypeError
        If ``df`` is not a polars/pandas DataFrame or a dict.
    """
    if isinstance(df, pl.DataFrame):
        return df
    if isinstance(df, pd.DataFrame):
        return pl.from_pandas(df)
    if isinstance(df, dict):
        return pl.DataFrame(df)
    raise TypeError(f"Expected DataFrame or dict, got {type(df).__name__}")


def dataframe_to_parquet(df: pl.DataFrame) -> bytes:
    """Serialise a polars DataFrame to parquet bytes (zstd compression)."""
    buffer = io.BytesIO()
    df.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


#: Filename and MIME type used when uploading a serialised DataFrame. The
#: backend detects parquet by either the ``.parquet`` extension or this
#: content type (see ``backend/src/utils/tabular_dataset.py``).
_PARQUET_FILENAME = "data.parquet"
_PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"


def parquet_file_part(df: pl.DataFrame) -> dict[str, tuple[str, bytes, str]]:
    """Build the ``files`` mapping for a multipart parquet upload.

    Parameters
    ----------
    df : pl.DataFrame
        The data to serialise and upload.

    Returns
    -------
    dict[str, tuple[str, bytes, str]]
        A ``requests``-style ``files`` mapping ready to pass to
        :meth:`~ionworks.client.Ionworks.upload_multipart`.
    """
    return {
        "file": (_PARQUET_FILENAME, dataframe_to_parquet(df), _PARQUET_CONTENT_TYPE)
    }


@contextmanager
def parquet_upload_body(df: pl.DataFrame) -> Iterator[tuple[IO[bytes], int]]:
    """Serialise ``df`` to parquet and yield an upload body with its byte length.

    Small frames serialise in memory, exactly as before. A frame at or above
    ``PARQUET_SPILL_THRESHOLD_BYTES`` is written to a temporary file and the
    open handle is yielded instead, for two reasons:

    - ``dataframe_to_parquet`` holds the whole parquet in RAM, on top of the
      frame it was built from.
    - s3transfer reads each part of a *file object* into a fresh in-memory
      buffer (bounded only by ``max_in_memory_upload_chunks``, which boto3 does
      not expose), where a handle backed by a real file streams per part. See
      ``_uploads._filesystem_path``.

    The temporary file is removed when the context exits, including on error.

    Parameters
    ----------
    df : pl.DataFrame
        Frame to serialise.

    Yields
    ------
    tuple[IO[bytes], int]
        A seekable binary handle positioned at the start, and the exact number
        of parquet bytes behind it. The size always comes from the same
        serialisation as the body, so the stored object and any recorded
        ``size_bytes`` cannot disagree.
    """
    if df.estimated_size() < PARQUET_SPILL_THRESHOLD_BYTES:
        data = dataframe_to_parquet(df)
        yield io.BytesIO(data), len(data)
        return

    # delete=False and an explicit close before reopening: on Windows a
    # NamedTemporaryFile cannot be opened a second time while its own handle
    # is still open, and polars writes by path.
    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    tmp.close()
    try:
        df.write_parquet(tmp.name, compression="zstd")
        with open(tmp.name, "rb") as handle:
            yield handle, os.path.getsize(tmp.name)
    finally:
        # Unlink only after the handle above is closed — Windows refuses to
        # remove a file that still has an open handle.
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def parquet_to_dataframe(content: bytes | str) -> DataFrame:
    """Read parquet into a DataFrame using the active backend.

    Parameters
    ----------
    content : bytes | str
        Raw parquet bytes, or a path/URL that :func:`polars.read_parquet`
        can read directly (e.g. a signed download URL).

    Returns
    -------
    DataFrame
        A polars or pandas DataFrame per the active backend
        (see :func:`ionworks.get_dataframe_backend`).
    """
    source = io.BytesIO(content) if isinstance(content, bytes) else content
    df = pl.read_parquet(source)
    if get_dataframe_backend() == "pandas":
        return df.to_pandas()
    return df
