"""Shared DataFrame <-> parquet helpers for SDK clients.

Small, dependency-light utilities used by clients that upload/download tabular
data as parquet (e.g. :mod:`ionworks.analysis`). Kept here so the conversion
logic lives in one place rather than being copied per client.
"""

from __future__ import annotations

import io

import pandas as pd
import polars as pl

from .validators import DataFrame, get_dataframe_backend


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
