"""Derived simulation columns, rebuilt client-side.

Parquet stores discharge/charge capacity reset per step, while clients expect
``Capacity [A.h]`` / ``Step capacity [A.h]`` on time series and ``Delta
capacity/energy`` on steps.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ._dataframe import to_polars
from .validators import DataFrame, get_dataframe_backend


def ensure_time_series_capacity_columns(df: DataFrame) -> DataFrame:
    """Add ``Step capacity [A.h]`` and ``Capacity [A.h]`` when possible.

    Parameters
    ----------
    df : DataFrame
        Time-series frame (polars or pandas) from ``time_series.parquet``.

    Returns
    -------
    DataFrame
        Same backend as input, with capacity columns filled when discharge and
        charge capacity columns are present.
    """
    pl_df = to_polars(df)
    if (
        "Discharge capacity [A.h]" not in pl_df.columns
        or "Charge capacity [A.h]" not in pl_df.columns
    ):
        return df

    if "Step capacity [A.h]" not in pl_df.columns:
        pl_df = pl_df.with_columns(
            (
                pl.col("Discharge capacity [A.h]") - pl.col("Charge capacity [A.h]")
            ).alias("Step capacity [A.h]")
        )

    if "Capacity [A.h]" not in pl_df.columns:
        if "Step count" in pl_df.columns:
            # Chain per-step discharge/charge ends into a continuous capacity.
            step_ends = (
                pl_df.group_by("Step count", maintain_order=True)
                .agg(
                    pl.col("Discharge capacity [A.h]").last().alias("d_end"),
                    pl.col("Charge capacity [A.h]").last().alias("c_end"),
                )
                .sort("Step count")
                .with_columns(
                    pl.col("d_end").cum_sum().shift(1).fill_null(0).alias("d_off"),
                    pl.col("c_end").cum_sum().shift(1).fill_null(0).alias("c_off"),
                )
                .select(["Step count", "d_off", "c_off"])
            )
            pl_df = (
                # Without maintain_order polars may reorder rows, silently
                # scrambling the time series.
                pl_df.join(
                    step_ends, on="Step count", how="left", maintain_order="left"
                )
                .with_columns(
                    (
                        pl.col("Discharge capacity [A.h]")
                        + pl.col("d_off")
                        - pl.col("Charge capacity [A.h]")
                        - pl.col("c_off")
                    ).alias("Capacity [A.h]")
                )
                .drop(["d_off", "c_off"])
            )
        else:
            pl_df = pl_df.with_columns(
                pl.col("Step capacity [A.h]").alias("Capacity [A.h]")
            )

    if get_dataframe_backend() == "pandas":
        return pl_df.to_pandas()
    return pl_df


def ensure_steps_delta_columns(df: DataFrame) -> DataFrame:
    """Add ``Delta capacity [A.h]`` and ``Delta energy [W.h]`` when possible.

    Parameters
    ----------
    df : DataFrame
        Steps frame from ``steps.parquet``.

    Returns
    -------
    DataFrame
        Same backend as input, with delta columns when sources exist.
    """
    pl_df = to_polars(df)
    exprs: list[Any] = []
    if (
        "Discharge capacity [A.h]" in pl_df.columns
        or "Charge capacity [A.h]" in pl_df.columns
    ) and "Delta capacity [A.h]" not in pl_df.columns:
        d = (
            pl.col("Discharge capacity [A.h]")
            if "Discharge capacity [A.h]" in pl_df.columns
            else pl.lit(0.0)
        )
        c = (
            pl.col("Charge capacity [A.h]")
            if "Charge capacity [A.h]" in pl_df.columns
            else pl.lit(0.0)
        )
        exprs.append((d - c).alias("Delta capacity [A.h]"))
    if (
        "Discharge energy [W.h]" in pl_df.columns
        or "Charge energy [W.h]" in pl_df.columns
    ) and "Delta energy [W.h]" not in pl_df.columns:
        d = (
            pl.col("Discharge energy [W.h]")
            if "Discharge energy [W.h]" in pl_df.columns
            else pl.lit(0.0)
        )
        c = (
            pl.col("Charge energy [W.h]")
            if "Charge energy [W.h]" in pl_df.columns
            else pl.lit(0.0)
        )
        exprs.append((d - c).alias("Delta energy [W.h]"))
    if exprs:
        pl_df = pl_df.with_columns(exprs)
    if get_dataframe_backend() == "pandas":
        return pl_df.to_pandas()
    return pl_df
