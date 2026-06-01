"""Reusable validator functions and composable pipelines for value normalization.

Provides functions for composable inbound/outbound value normalization
(e.g., converting between pandas DataFrames and dictionaries).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
import math
import os
import pathlib
from typing import Any, Literal
import warnings

import numpy as np
import pandas as pd
import polars as pl
from scipy.integrate import cumulative_trapezoid, trapezoid
from scipy.optimize import lsq_linear

from .errors import IonworksError

# --- DataFrame Backend Configuration ---------------------------------------- #

# Type alias for DataFrame (pandas or polars)
DataFrame = pd.DataFrame | pl.DataFrame


def _get_default_backend() -> str:
    """Get default backend from environment variable or fall back to 'polars'."""
    env_val = os.getenv("IONWORKS_DATAFRAME_BACKEND", "polars").lower()
    if env_val not in ("polars", "pandas"):
        return "polars"
    return env_val


# Module-level configuration for DataFrame return type
# Initialized from IONWORKS_DATAFRAME_BACKEND env var, defaults to "polars"
_dataframe_backend: str = _get_default_backend()


def set_dataframe_backend(backend: str) -> None:
    """Set the default DataFrame backend for data fetching.

    This overrides the IONWORKS_DATAFRAME_BACKEND environment variable.

    Parameters
    ----------
    backend : str
        DataFrame backend to use: "polars" or "pandas".

    Raises
    ------
    ValueError
        If backend is not "polars" or "pandas".
    """
    global _dataframe_backend
    if backend not in ("polars", "pandas"):
        raise ValueError(f"backend must be 'polars' or 'pandas', got '{backend}'")
    _dataframe_backend = backend


def get_dataframe_backend() -> str:
    """Get the current DataFrame backend setting.

    Returns
    -------
    str
        Current backend: "polars" or "pandas".
    """
    return _dataframe_backend


# --- Measurement Data Validators -------------------------------------------- #


Severity = Literal["error", "warning"]

#: Default relative-error tolerance for capacity/energy integral checks.
#: Used by :func:`validate_capacity_energy_from_current_power` and by
#: ``ionworksdata.transform.fix_swapped_charge_discharge_columns`` so both
#: agree on the same definition of "within tolerance".
CAPACITY_ENERGY_INTEGRAL_TOLERANCE: float = 0.10


class IssueCode(StrEnum):
    """Stable identifiers for measurement validation findings.

    Use these — not substrings of the human-readable ``message`` — when
    branching on issue identity. Members are ``str``-compatible.
    """

    CURRENT_SIGN_REVERSED = "current_sign_reversed"
    CURRENT_SIGN_INDETERMINATE = "current_sign_indeterminate"
    CUMULATIVE_VALUE_NOT_RESET = "cumulative_value_not_reset"
    CUMULATIVE_VALUE_DECREASED = "cumulative_value_decreased"
    STEP_TOO_FEW_POINTS = "step_too_few_points"
    TIME_DOES_NOT_START_AT_ZERO = "time_does_not_start_at_zero"
    TIME_NOT_MONOTONIC = "time_not_monotonic"
    STEP_COUNT_MISSING = "step_count_missing"
    STEP_COUNT_DOES_NOT_START_AT_ZERO = "step_count_does_not_start_at_zero"
    STEP_COUNT_NON_SEQUENTIAL = "step_count_non_sequential"
    CYCLE_CHANGES_WITHIN_STEP = "cycle_changes_within_step"
    OCP_VOLTAGE_COLUMN_MISSING = "ocp_voltage_column_missing"
    OCP_X_AXIS_COLUMN_MISSING = "ocp_x_axis_column_missing"
    TIME_SERIES_ROW_COUNT_EXCEEDED = "time_series_row_count_exceeded"
    TIME_GAP_TOO_LARGE = "time_gap_too_large"
    VOLTAGE_CONTINUITY = "voltage_continuity"
    CONSECUTIVE_SAME_DIRECTION_FULL_STEPS = "consecutive_same_direction_full_steps"
    STEP_CAPACITY_EXCEEDS_RATED = "step_capacity_exceeds_rated"
    DISCHARGE_CAPACITY_INTEGRAL_MISMATCH = "discharge_capacity_integral_mismatch"
    CHARGE_CAPACITY_INTEGRAL_MISMATCH = "charge_capacity_integral_mismatch"
    DISCHARGE_ENERGY_INTEGRAL_MISMATCH = "discharge_energy_integral_mismatch"
    CHARGE_ENERGY_INTEGRAL_MISMATCH = "charge_energy_integral_mismatch"


@dataclass(frozen=True)
class ValidationIssue:
    """Structured measurement validation finding.

    Returned by the ``validate_*`` functions and carried by
    :class:`MeasurementValidationError`. Branch on ``code`` rather than
    parsing ``message``; messages are for display only and may be reworded
    between releases.

    Parameters
    ----------
    code : IssueCode
        Stable identifier of the finding.
    message : str
        Human-readable description of the failure, including any fix hint.
    severity : {"error", "warning"}, optional
        ``"error"`` (the default) causes :func:`validate_measurement_data`
        to raise. ``"warning"`` is emitted via :func:`warnings.warn` and is
        **not** attached to the resulting :class:`MeasurementValidationError`
        — callers who need programmatic access to warning-severity issues
        must invoke the underlying ``validate_*`` producer directly rather
        than relying on ``e.has_code(...)``.
    payload : dict, optional
        Structured details: step indices, column names, observed values,
        thresholds. Keys depend on ``code``.
    """

    code: IssueCode
    message: str
    severity: Severity = "error"
    payload: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class MeasurementValidationError(IonworksError):
    """Exception raised when measurement data validation fails.

    ``errors`` is a list of structured :class:`ValidationIssue` records.
    Branch on ``issue.code`` rather than parsing ``issue.message``.
    """

    def __init__(
        self,
        message: str,
        errors: list[ValidationIssue] | None = None,
    ) -> None:
        super().__init__(message)
        self.errors: list[ValidationIssue] = errors or []

    def has_code(self, code: IssueCode) -> bool:
        """Return True if any issue matches ``code``."""
        return any(issue.code == code for issue in self.errors)


def _to_py_scalar(value: Any) -> Any:
    """Return a native Python scalar for ``value`` if it has ``.item()``.

    ``np.int64(0).item()`` returns ``0`` (Python ``int``); ``0.item()``
    doesn't exist. Use this when copying a single element out of a numpy
    array into a payload that will be JSON-serialised, so callers see
    plain ``int``/``float``/``bool`` rather than numpy scalar types.
    """
    return value.item() if hasattr(value, "item") else value


def _get_column(df: DataFrame, col: str) -> np.ndarray:
    """
    Extract a column as a numpy array from either pandas or polars DataFrame.

    Parameters
    ----------
    df : DataFrame
        pandas or polars DataFrame.
    col : str
        Column name.

    Returns
    -------
    np.ndarray
        Column values as numpy array.
    """
    if isinstance(df, pl.DataFrame):
        return df.get_column(col).to_numpy()
    return df[col].to_numpy()


def _has_column(df: DataFrame, col: str) -> bool:
    """Check if a column exists in the DataFrame."""
    return col in df.columns


def _get_step_group_indices(step_data: np.ndarray) -> np.ndarray:
    """Compute step group indices for each row (0-indexed, based on contiguous groups).

    Parameters
    ----------
    step_data : np.ndarray
        Array of step numbers/identifiers.

    Returns
    -------
    np.ndarray
        Array where each element is the step group index (0, 1, 2, ...) for
        that row.
    """
    changes = np.concatenate([[True], np.diff(step_data) != 0])
    return np.cumsum(changes) - 1


def _fit_ecm_convention(
    t: np.ndarray,
    I_data: np.ndarray,
    voltage: np.ndarray,
) -> tuple[float, float, float] | None:
    """Fit an OCV-R ECM under one sign convention hypothesis.

    Model: ``V = v0 + delta * soc - I_data * R0`` with ``delta >= 0``
    (monotone OCV) and ``R0 >= 0`` (positive resistance).

    Parameters
    ----------
    t : np.ndarray
        Time values [s].
    I_data : np.ndarray
        Signed current [A] for this convention hypothesis.
    voltage : np.ndarray
        Measured terminal voltage [V].

    Returns
    -------
    tuple[float, float, float] | None
        ``(delta, sse, R0)`` if the fit succeeded, or ``None`` if SOC
        range is degenerate.
    """
    # SOC: charge entering the cell increases SOC. In ECM convention
    # discharge current (I_data > 0) *decreases* SOC, so integrate -I.
    charge = cumulative_trapezoid(-I_data, t, initial=0)

    # Normalize to [0, 1] using min/max so SOC is always in range
    c_min, c_max = charge.min(), charge.max()
    span = c_max - c_min
    if span < 1e-12:
        return None
    soc = (charge - c_min) / span

    # Design matrix: V = v0*1 + delta*soc + R0*(-I_data)
    # After increment transform, columns are [1, soc, -I_data]
    neg_I = -I_data

    v_min, v_max = float(voltage.min()), float(voltage.max())
    v_range = max(v_max - v_min, 0.01)
    lb = np.array([v_min - v_range, 0.0, 0.0], dtype=np.float64)
    ub = np.array([v_max + v_range, 2.0 * v_range, np.inf], dtype=np.float64)

    A = np.column_stack((np.ones_like(soc), soc, neg_I))
    b = np.asarray(voltage, dtype=np.float64).ravel()
    result = lsq_linear(A, b, bounds=(lb, ub), method="bvls")
    v0, delta, R0 = result.x
    sse = float(result.cost)
    return delta, sse, R0


def positive_current_is_charge(
    t: np.ndarray,
    current: np.ndarray,
    voltage: np.ndarray,
) -> tuple[bool, float]:
    """Determine whether positive current corresponds to charging.

    Fits an OCV-R equivalent-circuit model ``V = OCV(SOC) - I * R0`` under
    two sign-convention hypotheses (positive = discharge vs. positive =
    charge). OCV is linear in SOC, constrained to a non-negative slope
    (monotonically increasing); R0 is a single positive scalar. When the
    sign convention is wrong the SOC axis is inverted, forcing the OCV
    slope toward zero. The convention producing the larger OCV slope is
    selected.

    Parameters
    ----------
    t : np.ndarray
        Time values [s].
    current : np.ndarray
        Current values [A].
    voltage : np.ndarray
        Voltage values [V].

    Returns
    -------
    is_charge : bool
        ``True`` if positive current is charging, ``False`` if
        discharging. Returns ``False`` when there is insufficient data.
    p_value : float
        Confidence metric in [0, 1]. Lower values indicate higher confidence.
        Computed as the sum of two ratios clipped to [0, 1]: the ratio of the
        smaller to the larger OCV slope (``delta_ratio``) and the ratio of the
        winner's SSE to the loser's SSE (``sse_ratio``). Returns 1.0 when the
        result is ambiguous or data is insufficient.
    """
    t = np.asarray(t, dtype=float)
    current = np.asarray(current, dtype=float)
    voltage = np.asarray(voltage, dtype=float)

    if t.size < 2 or t.max() == t.min() or np.linalg.norm(current, ord=np.inf) < 1e-12:
        return False, 1.0

    # 3 dof
    dof = len(t) - 3
    if dof <= 0:
        Q = cumulative_trapezoid(y=current, x=t, initial=0)
        if len(t) < 2 or Q[1] == Q[0]:
            return False, 1.0
        slope = (voltage[1] - voltage[0]) / (Q[1] - Q[0])
        return bool(slope >= 0), 1.0

    # Flat voltage: OCV slope is meaningless; treat as ambiguous (match ECM tie-break).
    v_span = float(np.ptp(voltage))
    v_ref = max(float(np.max(np.abs(voltage))), 1.0)
    if v_span <= max(1e-9, 1e-6 * v_ref):
        return True, 1.0

    # Fit under both sign conventions
    # Case A: positive current = discharge  (I_data = +current)
    # Case B: positive current = charge     (I_data = -current)
    fit_dis = _fit_ecm_convention(t, current, voltage)
    fit_chg = _fit_ecm_convention(t, -current, voltage)

    if fit_dis is None and fit_chg is None:
        return False, 1.0
    if fit_dis is None:
        return True, 1.0
    if fit_chg is None:
        return False, 1.0

    delta_dis, sse_dis, _ = fit_dis
    delta_chg, sse_chg, _ = fit_chg

    # Both deltas essentially zero → ambiguous (e.g. flat voltage)
    eps = 1e-10
    delta_max = max(delta_dis, delta_chg)
    if delta_max < eps:
        # Fall back: same as flat-voltage case, default to charge=True
        return True, 1.0

    # The convention with the larger OCV slope wins
    is_charge = bool(delta_chg > delta_dis)

    # Confidence from two signals:
    # 1) delta_ratio: how much the loser's slope collapsed (near 0 = clear)
    # 2) sse_ratio: how well the winner explains the data vs the loser
    delta_ratio = min(delta_dis, delta_chg) / max(delta_dis, delta_chg, eps)
    sse_winner = sse_chg if is_charge else sse_dis
    sse_loser = sse_dis if is_charge else sse_chg
    sse_ratio = sse_winner / max(sse_loser, eps) if sse_loser > eps else 0.0

    # p_value: product of both ratios. Low when the winner clearly
    # dominates on both OCV slope and goodness-of-fit.
    p_value = float(np.clip(delta_ratio + sse_ratio, 0.0, 1.0))

    return is_charge, p_value


def validate_positive_current_is_discharge(  # noqa: PLR0913
    df: DataFrame,
    current_col: str = "Current [A]",
    voltage_col: str = "Voltage [V]",
    time_col: str = "Time [s]",
    step_col: str | None = None,
    rest_tol: float = 1e-3,
    relative_rest_tol_frac: float = 0.02,
    relative_rest_tol_percentile: float = 95.0,
) -> list[ValidationIssue]:
    """
    Validate that positive current corresponds to discharge.

    Discharge should cause voltage to decrease. This function analyzes the
    relationship between current direction and voltage change to verify the
    sign convention is correct.

    Fits an OCV-R ECM per step, then uses a confidence vote across steps
    weighted by the trapezoidal integral of ``|I(t)| dt`` over each step
    so that steps actually moving charge dominate the decision and long
    near-zero-current voltage holds contribute negligible weight.

    Parameters
    ----------
    df : DataFrame
        Time series data with current and voltage columns (pandas or polars).
    current_col : str
        Name of the current column.
    voltage_col : str
        Name of the voltage column.
    time_col : str
        Name of the time column.
    step_col : str, optional
        Name of the step column. If provided, analyzes per-step. Otherwise,
        infers steps from current sign changes.
    rest_tol : float
        Tolerance for considering current as zero (rest).
    relative_rest_tol_frac : float
        Fraction of a robust current scale used to set a relative rest threshold.
        The robust scale is the percentile of ``abs(current)`` given by
        ``relative_rest_tol_percentile``.
    relative_rest_tol_percentile : float
        Percentile in [0, 100] used to estimate the robust current scale for the
        relative threshold.
        The effective rest threshold is ``min(rest_tol, relative_rest_tol)`` so the
        stricter (smaller) threshold takes precedence.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if not _has_column(df, current_col) or not _has_column(df, voltage_col):
        return []
    if not _has_column(df, time_col):
        return []

    current = _get_column(df, current_col)
    voltage = _get_column(df, voltage_col)
    time = _get_column(df, time_col)

    if len(current) == 0:
        return []

    # Use a robust relative threshold so low-current datasets don't get
    # treated as all-rest simply because they are below a fixed absolute floor.
    abs_current = np.abs(current)
    robust_scale = float(np.percentile(abs_current, relative_rest_tol_percentile))
    relative_rest_tol = max(relative_rest_tol_frac * robust_scale, 1e-12)
    # Precedence rule: use the stricter (smaller) rest threshold.
    effective_rest_tol = min(rest_tol, relative_rest_tol)

    # Determine step groups
    if step_col and _has_column(df, step_col):
        step_data = _get_column(df, step_col)
    else:
        # Infer steps from current sign changes
        max_abs = np.max(np.abs(current))
        if max_abs == 0:
            return []
        normalized = current / max_abs
        step_data = np.sign(normalized * (np.abs(normalized) > effective_rest_tol))

    step_groups = _get_step_group_indices(step_data)
    num_steps = step_groups[-1] + 1

    # Mean current per step (for rest filtering)
    step_current_sum = np.bincount(step_groups, weights=current, minlength=num_steps)
    step_counts = np.bincount(step_groups, minlength=num_steps).astype(float)
    step_counts[step_counts == 0] = 1
    mean_current = step_current_sum / step_counts

    # Identify non-rest steps
    non_rest_steps = set(np.where(np.abs(mean_current) >= effective_rest_tol)[0])
    if not non_rest_steps:
        return []

    # Classify each non-rest step using an OCV-R ECM fit. Weight each
    # step's vote by the trapezoidal integral of ``|I(t)| dt`` over the
    # step so that steps actually moving charge dominate the decision and
    # long near-zero-current voltage holds contribute negligible weight.
    # Flag a sign-convention error when at least 75% of the weighted
    # evidence points to charge, and return an ambiguous error when it
    # is between 25% and 75%.
    charge_weight = 0.0
    discharge_weight = 0.0

    for step_id in non_rest_steps:
        mask = step_groups == step_id
        t_step = time[mask]
        i_step = current[mask]
        if t_step.size < 2:
            continue
        charge_passed = float(trapezoid(np.abs(i_step), t_step))
        if charge_passed <= 0:
            continue
        is_charge, p_value = positive_current_is_charge(t_step, i_step, voltage[mask])
        confidence = 1.0 - p_value
        weight = confidence * charge_passed
        if is_charge:
            charge_weight += weight
        else:
            discharge_weight += weight

    total_weight = charge_weight + discharge_weight
    if total_weight <= 0:
        return []

    charge_fraction = charge_weight / total_weight
    if charge_fraction >= 0.75:
        return [
            ValidationIssue(
                IssueCode.CURRENT_SIGN_REVERSED,
                "Current sign convention error: positive current appears to "
                "be charge, not discharge. Voltage increases when current is "
                "positive, but for discharge, voltage should decrease. Use "
                "ionworksdata.transform.set_positive_current_for_discharge"
                "(data) to fix this.",
                payload={"charge_fraction": charge_fraction},
            )
        ]
    elif charge_fraction >= 0.25:
        return [
            ValidationIssue(
                IssueCode.CURRENT_SIGN_INDETERMINATE,
                "Current sign convention error: the sign convention is "
                "ambiguous. Check if all currents are positive or negative.",
                payload={"charge_fraction": charge_fraction},
            )
        ]
    else:
        return []


def validate_cumulative_values_reset_per_step(
    df: DataFrame,
    step_col: str = "Step count",
    cumulative_cols: list[str] | None = None,
    tolerance: float = 1e-6,
) -> list[ValidationIssue]:
    """Validate cumulative values reset to ~0 at each step and only increase.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    step_col : str
        Name of the column containing step numbers.
    cumulative_cols : list[str], optional
        List of cumulative column names to validate. If None, checks for common
        capacity and energy columns.
    tolerance : float
        Tolerance for considering a value as "zero" at step start.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    errors: list[ValidationIssue] = []

    if not _has_column(df, step_col):
        return []

    if cumulative_cols is None:
        cumulative_cols = [
            "Discharge capacity [A.h]",
            "Charge capacity [A.h]",
            "Discharge energy [W.h]",
            "Charge energy [W.h]",
        ]

    cols_to_check = [col for col in cumulative_cols if _has_column(df, col)]
    if not cols_to_check:
        return []

    fix_hint = (
        "Use ionworksdata.transform.set_capacity(data) and/or "
        "ionworksdata.transform.set_energy(data) to fix this."
    )

    step_data = _get_column(df, step_col)
    if len(step_data) == 0:
        return []
    step_groups = _get_step_group_indices(step_data)

    # Find step boundaries (first index of each step)
    step_boundaries = np.where(np.diff(step_groups, prepend=-1) != 0)[0]

    for col in cols_to_check:
        values = _get_column(df, col)

        # Check 1: Values at step starts should be ~0
        start_values = values[step_boundaries]
        non_zero_mask = np.abs(start_values) > tolerance
        non_zero_steps = np.where(non_zero_mask)[0]

        for step_idx in non_zero_steps:
            observed = float(start_values[step_idx])
            errors.append(
                ValidationIssue(
                    IssueCode.CUMULATIVE_VALUE_NOT_RESET,
                    f"Column '{col}' does not reset at start of "
                    f"step {step_idx}: expected ~0, got "
                    f"{observed:.6f}. Cumulative values "
                    f"should reset to 0 at the start of each step. "
                    f"{fix_hint}",
                    payload={
                        "column": col,
                        "step_index": int(step_idx),
                        "observed": observed,
                        "tolerance": tolerance,
                    },
                )
            )

        # Check 2: Values should be monotonically non-decreasing within each step
        # Compute diff and check where it's negative within same step
        value_diffs = np.diff(values, prepend=values[0])
        step_diffs = np.diff(step_groups, prepend=step_groups[0])

        # Mask: same step (diff == 0) and value decreased
        within_step = step_diffs == 0
        decreased = value_diffs < -tolerance

        # Find first decrease per step
        problem_indices = np.where(within_step & decreased)[0]
        if len(problem_indices) > 0:
            # Group by step and report first decrease per step
            problem_steps = step_groups[problem_indices]
            unique_problem_steps = np.unique(problem_steps)

            for step_idx in unique_problem_steps:
                step_problem_indices = problem_indices[problem_steps == step_idx]
                first_idx = int(step_problem_indices[0])
                prev_value = float(values[first_idx - 1])
                curr_value = float(values[first_idx])
                errors.append(
                    ValidationIssue(
                        IssueCode.CUMULATIVE_VALUE_DECREASED,
                        f"Column '{col}' decreases within step "
                        f"{step_idx} at index {first_idx}: value went "
                        f"from {prev_value:.6f} to "
                        f"{curr_value:.6f}. Cumulative values "
                        f"should only increase within a step. "
                        f"{fix_hint}",
                        payload={
                            "column": col,
                            "step_index": int(step_idx),
                            "row_index": first_idx,
                            "previous_value": prev_value,
                            "current_value": curr_value,
                        },
                    )
                )

    return errors


def validate_minimum_points_per_step(
    df: DataFrame,
    step_col: str = "Step count",
    min_points: int = 2,
) -> list[ValidationIssue]:
    """
    Validate that each step has at least a minimum number of data points.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    step_col : str
        Name of the column containing step numbers.
    min_points : int
        Minimum number of points required per step.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if not _has_column(df, step_col):
        return []

    step_data = _get_column(df, step_col)
    if len(step_data) == 0:
        return []

    step_groups = _get_step_group_indices(step_data)
    num_steps = step_groups[-1] + 1

    # Vectorized count per step
    step_counts = np.bincount(step_groups, minlength=num_steps)

    # Find steps with insufficient points
    insufficient_mask = step_counts < min_points
    insufficient_steps = np.where(insufficient_mask)[0]

    errors: list[ValidationIssue] = []
    for step_idx in insufficient_steps:
        num_points = int(step_counts[step_idx])
        errors.append(
            ValidationIssue(
                IssueCode.STEP_TOO_FEW_POINTS,
                f"Step {step_idx} has only {num_points} data point(s), "
                f"but at least {min_points} are required.",
                payload={
                    "step_index": int(step_idx),
                    "num_points": num_points,
                    "min_points": min_points,
                },
            )
        )

    return errors


def validate_time_starts_at_zero(
    df: DataFrame,
    tolerance: float = 1e-6,
) -> list[ValidationIssue]:
    """Validate that 'Time [s]' starts at 0.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    tolerance : float
        Tolerance for considering the start value as zero.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    time_col = "Time [s]"
    if not _has_column(df, time_col):
        return []

    time_data = _get_column(df, time_col)
    if len(time_data) == 0:
        return []

    first = float(time_data[0])
    if abs(first) > tolerance:
        return [
            ValidationIssue(
                IssueCode.TIME_DOES_NOT_START_AT_ZERO,
                f"Column '{time_col}' must start at 0, but starts at "
                f"{first}. Use ionworksdata.transform.reset_time"
                f"(data) to fix this. To indicate the absolute time when a "
                f"step starts, use the start_time field in the measurement "
                f"metadata.",
                payload={"observed": first, "tolerance": tolerance},
            )
        ]

    return []


def validate_time_monotonic(
    df: DataFrame,
    time_col: str = "Time [s]",
    tolerance: float = 1e-12,
) -> list[ValidationIssue]:
    """Validate that the time column is monotonically non-decreasing.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    time_col : str
        Name of the time column.
    tolerance : float
        Numerical tolerance; time[i] must be >= time[i-1] - tolerance.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if not _has_column(df, time_col):
        return []

    time_data = _get_column(df, time_col)
    if len(time_data) < 2:
        return []

    diffs = np.diff(time_data)
    bad_mask = diffs < -tolerance
    if not np.any(bad_mask):
        return []

    bad_indices = np.where(bad_mask)[0]
    first_idx = int(bad_indices[0])
    prev_t = float(time_data[first_idx])
    next_t = float(time_data[first_idx + 1])
    return [
        ValidationIssue(
            IssueCode.TIME_NOT_MONOTONIC,
            f"Column '{time_col}' must be monotonically non-decreasing. "
            f"At index {first_idx + 1}: {next_t:.6f}s < "
            f"previous {prev_t:.6f}s. "
            f"Use a cumulative time series (e.g. ionworksdata.transform or "
            f"ensure per-step time is converted to global elapsed time).",
            payload={
                "row_index": first_idx + 1,
                "previous_time": prev_t,
                "current_time": next_t,
                "num_violations": int(bad_mask.sum()),
            },
        )
    ]


def validate_step_count_sequential(
    df: DataFrame,
) -> list[ValidationIssue]:
    """Validate that 'Step count' exists, starts at 0, and increases by 1.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    step_col = "Step count"

    fix_hint = "Use ionworksdata.transform.set_step_count(data) to fix this."

    if not _has_column(df, step_col):
        return [
            ValidationIssue(
                IssueCode.STEP_COUNT_MISSING,
                f"Column '{step_col}' is required but was not found in "
                f"the data. Available columns: {list(df.columns)}. "
                f"{fix_hint}",
                payload={
                    "column": step_col,
                    "available_columns": list(df.columns),
                },
            )
        ]

    step_data = _get_column(df, step_col)
    if len(step_data) == 0:
        return []

    errors: list[ValidationIssue] = []

    if step_data[0] != 0:
        first_val = _to_py_scalar(step_data[0])
        errors.append(
            ValidationIssue(
                IssueCode.STEP_COUNT_DOES_NOT_START_AT_ZERO,
                f"Column '{step_col}' must start at 0, but starts at "
                f"{first_val}. {fix_hint}",
                payload={"column": step_col, "observed_first": first_val},
            )
        )

    raw_diffs = np.diff(step_data)
    bad_mask = (raw_diffs != 0) & (raw_diffs != 1)
    if np.any(bad_mask):
        bad_indices = np.where(bad_mask)[0]
        examples = []
        # Use ``.item()`` (not ``int()``) so float Step counts like 0.5
        # round-trip to the payload instead of being truncated to 0.
        bad_transitions: list[dict[str, Any]] = []
        for idx in bad_indices[:5]:
            examples.append(
                f"index {idx}: {step_data[idx]} -> "
                f"{step_data[idx + 1]} (diff={raw_diffs[idx]})"
            )
            bad_transitions.append(
                {
                    "row_index": int(idx),
                    "from": _to_py_scalar(step_data[idx]),
                    "to": _to_py_scalar(step_data[idx + 1]),
                    "diff": _to_py_scalar(raw_diffs[idx]),
                }
            )
        more = ""
        if len(bad_indices) > 5:
            more = f" (and {len(bad_indices) - 5} more)"
        errors.append(
            ValidationIssue(
                IssueCode.STEP_COUNT_NON_SEQUENTIAL,
                f"Column '{step_col}' must increase by 1 at each step "
                f"transition, but found {len(bad_indices)} invalid "
                f"transition(s): " + "; ".join(examples) + f".{more} {fix_hint}",
                payload={
                    "column": step_col,
                    "num_violations": int(len(bad_indices)),
                    "examples": bad_transitions,
                },
            )
        )

    return errors


def validate_cycle_constant_within_step(
    df: DataFrame,
    step_col: str = "Step count",
    cycle_col: str | None = None,
) -> list[ValidationIssue]:
    """
    Validate that cycle number does not change within a step.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    step_col : str
        Name of the column containing step numbers.
    cycle_col : str, optional
        Name of the column containing cycle numbers. If None, tries common names.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if not _has_column(df, step_col):
        return []

    # Find cycle column
    if cycle_col is None:
        for col in ["Cycle count", "Cycle number", "Cycle from cycler"]:
            if _has_column(df, col):
                cycle_col = col
                break

    if cycle_col is None or not _has_column(df, cycle_col):
        return []

    step_data = _get_column(df, step_col)
    if len(step_data) == 0:
        return []

    cycle_data = _get_column(df, cycle_col)
    step_groups = _get_step_group_indices(step_data)

    # Detect cycle changes within steps:
    # A cycle change within a step occurs when:
    # - The cycle value differs from the previous row
    # - AND we're in the same step group
    cycle_diffs = np.diff(cycle_data, prepend=cycle_data[0])
    step_diffs = np.diff(step_groups, prepend=step_groups[0])

    # Within-step cycle change: same step (step_diff == 0) but cycle changed
    within_step_cycle_change = (step_diffs == 0) & (cycle_diffs != 0)

    problem_indices = np.where(within_step_cycle_change)[0]
    if len(problem_indices) == 0:
        return []

    # Group by step and report
    problem_steps = step_groups[problem_indices]
    unique_problem_steps = np.unique(problem_steps)

    errors: list[ValidationIssue] = []
    for step_idx in unique_problem_steps:
        step_mask = step_groups == step_idx
        unique_cycles = np.unique(cycle_data[step_mask])
        cycles_list = unique_cycles.tolist()
        errors.append(
            ValidationIssue(
                IssueCode.CYCLE_CHANGES_WITHIN_STEP,
                f"Cycle number changes within step {step_idx}: "
                f"found cycles {cycles_list}. "
                f"Each step should belong to a single cycle. "
                f"Use ionworksdata.transform.set_cycle_count(data) "
                f"to fix this.",
                payload={
                    "step_index": int(step_idx),
                    "cycle_column": cycle_col,
                    "cycles": cycles_list,
                },
            )
        )

    return errors


def validate_ocp_columns(df: DataFrame) -> list[ValidationIssue]:
    """Validate that OCP data has required columns.

    Checks that the DataFrame contains:
    1. A 'Voltage [V]' column
    2. At least one x-axis column: 'Capacity [A.h]', 'Stoichiometry', or 'SOC'

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    errors: list[ValidationIssue] = []
    available = list(df.columns)
    if not _has_column(df, "Voltage [V]"):
        errors.append(
            ValidationIssue(
                IssueCode.OCP_VOLTAGE_COLUMN_MISSING,
                "OCP data must contain a 'Voltage [V]' column. "
                f"Available columns: {available}",
                payload={"available_columns": available},
            )
        )
    x_axis_columns = ["Capacity [A.h]", "Stoichiometry", "SOC"]
    if not any(_has_column(df, col) for col in x_axis_columns):
        errors.append(
            ValidationIssue(
                IssueCode.OCP_X_AXIS_COLUMN_MISSING,
                "OCP data must contain at least one x-axis column: "
                f"{', '.join(x_axis_columns)}. "
                f"Available columns: {available}",
                payload={
                    "candidates": x_axis_columns,
                    "available_columns": available,
                },
            )
        )
    return errors


def validate_time_series_row_count(
    df: DataFrame,
    max_rows: int = 1000,
) -> list[ValidationIssue]:
    """Validate that the time series does not exceed the maximum row count.

    Datasets larger than ``max_rows`` should be uploaded via the standard
    upload flow and then referenced with ``"db:<measurement_id>"`` or
    ``iwdata.DataLoader.from_db(MEASUREMENT_ID)`` in pipeline configurations.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    max_rows : int
        Maximum allowed number of rows.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    n_rows = len(df)
    if n_rows > max_rows:
        return [
            ValidationIssue(
                IssueCode.TIME_SERIES_ROW_COUNT_EXCEEDED,
                f"Time series has {n_rows} rows, which exceeds the maximum "
                f"of {max_rows} rows for inline data. Upload the data as a "
                f"measurement using client.cell_measurement.create() and "
                f'reference it with "db:<measurement_id>" or '
                f"iwdata.DataLoader.from_db(MEASUREMENT_ID) instead.",
                payload={"n_rows": int(n_rows), "max_rows": int(max_rows)},
            )
        ]
    return []


def validate_time_gaps(
    df: DataFrame,
    time_col: str = "Time [s]",
    max_gap_seconds: float = 5 * 3600,
) -> list[ValidationIssue]:
    """Validate that there are no large gaps between consecutive time samples.

    A gap longer than ``max_gap_seconds`` between two consecutive rows is almost
    always a sign that a chunk of cycling was dropped — for example, a rest
    period was recorded as elapsed time in the file but the intermediate rows
    were stripped, leaving the capacity integral to count current across the
    unrecorded interval.

    Parameters
    ----------
    df : DataFrame
        Time series data with a time column (pandas or polars).
    time_col : str, optional
        Name of the time column. Defaults to ``"Time [s]"``.
    max_gap_seconds : float, optional
        Maximum allowed gap between consecutive time samples, in seconds.
        Defaults to ``5 * 3600`` (5 hours).

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if not _has_column(df, time_col):
        return []

    time_data = _get_column(df, time_col)
    if len(time_data) < 2:
        return []

    diffs = np.diff(np.asarray(time_data, dtype=float))
    bad_mask = diffs > max_gap_seconds
    if not np.any(bad_mask):
        return []

    bad_indices = np.where(bad_mask)[0]
    first_idx = int(bad_indices[0])
    gap_seconds = float(diffs[first_idx])
    gap_hours = gap_seconds / 3600.0
    num_violations = int(bad_mask.sum())
    more = f" (and {num_violations - 1} more)" if num_violations > 1 else ""
    return [
        ValidationIssue(
            IssueCode.TIME_GAP_TOO_LARGE,
            f"Time gap of {gap_hours:.2f} h between rows {first_idx} and "
            f"{first_idx + 1} exceeds the maximum allowed gap of "
            f"{max_gap_seconds / 3600:.1f} h.{more} Large time gaps "
            f"typically mean rows were dropped during processing; the "
            f"capacity integral will count current across the missing "
            f"interval.",
            payload={
                "row_index": first_idx,
                "gap_seconds": gap_seconds,
                "max_gap_seconds": float(max_gap_seconds),
                "num_violations": num_violations,
            },
        )
    ]


def validate_voltage_continuity(
    df: DataFrame,
    voltage_window: tuple[float, float],
    voltage_col: str = "Voltage [V]",
    jump_fraction: float = 0.80,
    max_bad_fraction: float = 0.05,
) -> list[ValidationIssue]:
    """Validate that consecutive rows do not exhibit unphysical voltage jumps.

    Computes the absolute voltage difference between every pair of consecutive rows
    and counts the fraction that exceed ``jump_fraction`` of the rated voltage window
    ``(V_max - V_min)``. If more than ``max_bad_fraction`` of row pairs exceed that
    threshold, the data is flagged as likely being out of chronological order (for
    example, after a faulty ``partition_by("Cycle_raw")`` grouping that interleaves
    pulse and rest rows from different cycles).

    Parameters
    ----------
    df : DataFrame
        Time series data with a voltage column (pandas or polars).
    voltage_window : tuple[float, float]
        ``(V_min, V_max)`` rated voltage window of the cell, typically the
        lower/upper cutoff voltages. Only the span ``V_max - V_min`` is used.
    voltage_col : str, optional
        Name of the voltage column. Defaults to ``"Voltage [V]"``.
    jump_fraction : float, optional
        Fraction of the voltage window considered the maximum physically plausible
        single-row voltage change. Defaults to ``0.80`` (80 %).
    max_bad_fraction : float, optional
        Maximum fraction of consecutive row pairs allowed to exceed the jump
        threshold before the check fails. Defaults to ``0.05`` (5 %).

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if not _has_column(df, voltage_col):
        return []

    voltage = _get_column(df, voltage_col)
    if len(voltage) < 2:
        return []

    v_min, v_max = float(voltage_window[0]), float(voltage_window[1])
    window = v_max - v_min
    if window <= 0:
        return []

    threshold = jump_fraction * window
    diffs = np.abs(np.diff(voltage))
    bad_count = int(np.sum(diffs > threshold))
    total_pairs = int(diffs.size)
    bad_fraction = bad_count / total_pairs

    if bad_fraction <= max_bad_fraction:
        return []

    return [
        ValidationIssue(
            IssueCode.VOLTAGE_CONTINUITY,
            f"Voltage continuity check failed: {bad_count}/{total_pairs} "
            f"({bad_fraction * 100:.1f}%) of consecutive row pairs have a "
            f"voltage jump greater than {jump_fraction * 100:.0f}% of the "
            f"rated voltage window ({threshold:.3f} V). This usually means "
            f"the time series is not in chronological order — check for "
            f'grouping operations such as ``partition_by("Cycle_raw")`` '
            f"that may destroy the natural row order.",
            payload={
                "bad_count": bad_count,
                "total_pairs": total_pairs,
                "bad_fraction": bad_fraction,
                "threshold_volts": float(threshold),
                "voltage_window": [v_min, v_max],
            },
        )
    ]


def _step_direction(step_type: str) -> str | None:
    """Map a ``Step type`` label to ``"charge"``, ``"discharge"``, or ``None``.

    ``None`` is returned for rest steps, EIS steps, and any unknown type so that
    rests do not reset the same-direction streak tracked by the consecutive
    full-step check.
    """
    if not isinstance(step_type, str):
        return None
    lowered = step_type.lower()
    if "discharge" in lowered:
        return "discharge"
    if "charge" in lowered:
        return "charge"
    return None


def validate_consecutive_same_direction_full_steps(
    steps_df: DataFrame,
    rated_capacity: float | None,
    step_type_col: str = "Step type",
    discharge_capacity_col: str = "Discharge capacity [A.h]",
    charge_capacity_col: str = "Charge capacity [A.h]",
    step_count_col: str = "Step count",
    full_step_fraction: float = 2.0,
) -> list[ValidationIssue]:
    """Validate that no two consecutive full-capacity steps share the same direction.

    Walks the step summary in order. For each constant-current step (identified by
    its ``Step type``) that delivers more than ``full_step_fraction`` of the rated
    capacity, tracks the direction. If two such steps appear consecutively with the
    same direction (ignoring rest / EIS / unknown steps, which do not reset the
    streak), the check fails. This catches measurements that bundle multiple
    independent experiments — e.g. a discharge-rate file concatenating several CC
    discharges into a single measurement.

    Parameters
    ----------
    steps_df : DataFrame
        Step summary dataframe (as produced by ``ionworksdata.steps.summarize``).
    rated_capacity : float | None
        Rated (nominal) cell capacity in A.h. Used together with
        ``full_step_fraction`` to decide whether a step counts as a full
        charge / discharge. The check is skipped when this is ``None`` or
        non-positive.
    step_type_col : str, optional
        Name of the column containing the step type labels (``"Rest"``,
        ``"Constant current discharge"``, etc.). Defaults to ``"Step type"``.
    discharge_capacity_col : str, optional
        Name of the per-step discharge capacity column.
    charge_capacity_col : str, optional
        Name of the per-step charge capacity column.
    step_count_col : str, optional
        Name of the per-step identifier column, reported in error messages.
    full_step_fraction : float, optional
        Multiple of rated capacity above which a step is considered a
        full (and then some) charge / discharge. Defaults to ``2.0`` — i.e.
        only steps delivering more than 2× rated capacity trigger the check,
        which tolerates one or two full cycles being captured in a single
        step while still catching runaway concatenations.

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if validation passes.
    """
    if rated_capacity is None or rated_capacity <= 0:
        return []
    if not _has_column(steps_df, step_type_col):
        return []
    if not _has_column(steps_df, discharge_capacity_col) or not _has_column(
        steps_df, charge_capacity_col
    ):
        return []

    step_types = _get_column(steps_df, step_type_col)
    discharge_caps = np.asarray(
        _get_column(steps_df, discharge_capacity_col), dtype=float
    )
    charge_caps = np.asarray(_get_column(steps_df, charge_capacity_col), dtype=float)
    step_counts = (
        _get_column(steps_df, step_count_col)
        if _has_column(steps_df, step_count_col)
        else np.arange(len(step_types))
    )

    threshold = full_step_fraction * rated_capacity

    errors: list[ValidationIssue] = []
    prev_direction: str | None = None
    prev_step_count: Any = None

    for i, step_type in enumerate(step_types):
        direction = _step_direction(step_type)
        if direction is None:
            continue

        raw = discharge_caps[i] if direction == "discharge" else charge_caps[i]
        delivered = 0.0 if np.isnan(raw) else abs(float(raw))

        is_full_step = delivered > threshold

        if is_full_step and direction == prev_direction:
            curr_step = step_counts[i]
            errors.append(
                ValidationIssue(
                    IssueCode.CONSECUTIVE_SAME_DIRECTION_FULL_STEPS,
                    f"Consecutive full-capacity {direction} steps detected: "
                    f"step {prev_step_count} and step {curr_step} each "
                    f"delivered more than {full_step_fraction * 100:.0f}% of "
                    f"the rated capacity ({threshold:.3f} A.h). A single "
                    f"measurement should not contain multiple independent "
                    f"{direction} experiments; split them into separate "
                    f"measurements.",
                    payload={
                        "direction": direction,
                        "previous_step": _to_py_scalar(prev_step_count),
                        "current_step": _to_py_scalar(curr_step),
                        "threshold_capacity": float(threshold),
                        "rated_capacity": float(rated_capacity),
                    },
                )
            )

        if is_full_step:
            prev_direction = direction
            prev_step_count = step_counts[i]

    return errors


def validate_step_capacity_within_rated(
    steps_df: DataFrame,
    rated_capacity: float | None,
    discharge_capacity_col: str = "Discharge capacity [A.h]",
    charge_capacity_col: str = "Charge capacity [A.h]",
    step_count_col: str = "Step count",
    max_ratio: float = 5.0,
) -> list[ValidationIssue]:
    """Soft-check that no single step exceeds ``max_ratio`` × rated capacity.

    A single step accumulating more capacity than several full charges or
    discharges of the cell usually indicates wrong step boundaries or that the
    capacity integral was inflated across unrecorded time gaps. Returns a list
    of warnings; callers may emit them via :func:`warnings.warn` instead of
    raising.

    Parameters
    ----------
    steps_df : DataFrame
        Step summary dataframe (as produced by ``ionworksdata.steps.summarize``).
    rated_capacity : float | None
        Rated (nominal) cell capacity in A.h. The check is skipped when this
        is ``None`` or non-positive.
    discharge_capacity_col : str, optional
        Name of the per-step discharge capacity column.
    charge_capacity_col : str, optional
        Name of the per-step charge capacity column.
    step_count_col : str, optional
        Name of the per-step identifier column, reported in warning messages.
    max_ratio : float, optional
        Maximum allowed ratio of per-step capacity to rated capacity.
        Defaults to ``5.0`` (500 %).

    Returns
    -------
    list[ValidationIssue]
        List of validation issues. Empty if no step exceeds the threshold.
    """
    if rated_capacity is None or rated_capacity <= 0:
        return []
    if not _has_column(steps_df, discharge_capacity_col) and not _has_column(
        steps_df, charge_capacity_col
    ):
        return []

    threshold = max_ratio * rated_capacity

    step_counts = (
        _get_column(steps_df, step_count_col)
        if _has_column(steps_df, step_count_col)
        else np.arange(len(steps_df))
    )

    total_count = 0
    first: tuple[Any, str, float] | None = None
    for col in (discharge_capacity_col, charge_capacity_col):
        if not _has_column(steps_df, col):
            continue
        abs_values = np.abs(np.asarray(_get_column(steps_df, col), dtype=float))
        over_mask = ~np.isnan(abs_values) & (abs_values > threshold)
        col_count = int(over_mask.sum())
        if col_count == 0:
            continue
        total_count += col_count
        if first is None:
            idx = int(np.argmax(over_mask))
            first = (step_counts[idx], col, float(abs_values[idx]))

    if first is None:
        return []

    first_step, first_col, first_value = first
    more = f" (and {total_count - 1} more)" if total_count > 1 else ""
    first_step_payload = _to_py_scalar(first_step)
    return [
        ValidationIssue(
            IssueCode.STEP_CAPACITY_EXCEEDS_RATED,
            f"{total_count} step(s) exceed {max_ratio * 100:.0f}% of the "
            f"rated capacity ({threshold:.3f} A.h). First: step "
            f"{first_step} has '{first_col}' = {first_value:.3f} A.h.{more} "
            f"This usually means step boundaries are wrong or the capacity "
            f"integral counted current across unrecorded time gaps.",
            payload={
                "total_count": total_count,
                "first_step": first_step_payload,
                "first_column": first_col,
                "first_value": first_value,
                "threshold_capacity": float(threshold),
                "rated_capacity": float(rated_capacity),
            },
            severity="warning",
        )
    ]


def step_boundary_idx(step_data: np.ndarray) -> np.ndarray:
    """Per-row index of the most recent step start.

    Subtracting ``series[step_boundary_idx(step_data)]`` from a running
    cumulative ``series`` resets it to 0 at every change in ``step_data``.
    Compute this once and pass it into multiple
    :func:`running_step_reset_integral` calls when they share the same
    step axis.

    Parameters
    ----------
    step_data : np.ndarray
        Per-row step identifier (e.g. the ``"Step count"`` column).

    Returns
    -------
    np.ndarray
        Index of the most recent step-start row for each row, same length
        as ``step_data``.
    """
    groups = _get_step_group_indices(step_data)
    starts = np.where(
        np.concatenate([[True], np.diff(groups) != 0]),
        np.arange(len(groups)),
        0,
    )
    return np.maximum.accumulate(starts)


def running_step_reset_integral(
    signed: np.ndarray,
    time: np.ndarray,
    step_data: np.ndarray | None = None,
    *,
    boundary_idx: np.ndarray | None = None,
) -> np.ndarray:
    """Cumulative trapezoidal integral of ``signed`` that resets at each step.

    Mirrors the reset semantics of the platform's cumulative capacity and
    energy columns so the returned series can be compared row-by-row.
    Divides by 3600 so the result is in A.h when ``signed`` is in A, or in
    W.h when ``signed`` is in W.

    Parameters
    ----------
    signed : np.ndarray
        Per-row signed values (e.g. ``max(I, 0)`` to integrate the
        discharge half-wave only).
    time : np.ndarray
        Time values [s]; same length as ``signed``.
    step_data : np.ndarray, optional
        Per-row step identifier (e.g. ``"Step count"`` column). The
        integral is reset to 0 at every change in this value. When
        ``None`` (and ``boundary_idx`` is also None), the integral is
        not reset.
    boundary_idx : np.ndarray, optional
        Output of :func:`step_boundary_idx` for the same step axis.
        Provide this to amortise the boundary computation across
        multiple integrals that share the same ``step_data``.

    Returns
    -------
    np.ndarray
        Running per-step integral, same length as ``signed``, reset to 0
        at the first row of each step.
    """
    integrated = cumulative_trapezoid(signed, time, initial=0.0) / 3600.0
    if boundary_idx is None:
        if step_data is None:
            return integrated
        boundary_idx = step_boundary_idx(step_data)
    return integrated - integrated[boundary_idx]


def worst_row_relative_error(
    reported: np.ndarray, integrated: np.ndarray
) -> tuple[int, float, float]:
    """Index, magnitude, and scale of the largest row-wise relative deviation.

    The relative error is the largest absolute deviation between the two
    series divided by the larger of their max-magnitudes (the scale). NaN
    rows are ignored. Returns ``(0, inf, 0.0)`` when there is nothing to
    compare (both series flat at zero, or all-NaN).

    Parameters
    ----------
    reported : np.ndarray
        Reported cumulative series.
    integrated : np.ndarray
        Integrated series to compare against, same length as ``reported``.

    Returns
    -------
    tuple[int, float, float]
        ``(worst_row_index, relative_error, max_scale)``.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        max_scale = float(
            np.fmax(np.nanmax(np.abs(integrated)), np.nanmax(np.abs(reported)))
        )
    if not np.isfinite(max_scale) or max_scale <= 0.0:
        return 0, float("inf"), 0.0
    diff = np.abs(reported - integrated)
    if not np.any(np.isfinite(diff)):
        return 0, float("inf"), max_scale
    worst_idx = int(np.nanargmax(diff))
    return worst_idx, float(diff[worst_idx]) / max_scale, max_scale


def validate_capacity_energy_from_current_power(
    df: DataFrame,
    step_col: str = "Step count",
    time_col: str = "Time [s]",
    current_col: str = "Current [A]",
    voltage_col: str = "Voltage [V]",
    power_col: str = "Power [W]",
    discharge_capacity_col: str = "Discharge capacity [A.h]",
    charge_capacity_col: str = "Charge capacity [A.h]",
    discharge_energy_col: str = "Discharge energy [W.h]",
    charge_energy_col: str = "Charge energy [W.h]",
    tolerance: float = CAPACITY_ENERGY_INTEGRAL_TOLERANCE,
) -> list[ValidationIssue]:
    """Validate cumulative capacity/energy columns match integrals of current/power.

    Builds a running trapezoidal integral over the whole time series and
    compares it row-by-row against the reported cumulative columns. The
    reported columns reset to 0 at the start of each step, so the integral
    is reset the same way and the comparison is made on the running
    within-step accumulator. With the platform sign convention (positive
    current = discharge):

    - Discharge capacity [A.h] ≈ ∫ max(I, 0) dt / 3600
    - Charge capacity [A.h]    ≈ ∫ max(-I, 0) dt / 3600
    - Discharge energy [W.h]   ≈ ∫ max(P, 0) dt / 3600
    - Charge energy [W.h]      ≈ ∫ max(-P, 0) dt / 3600

    The reported series and the integrated series are compared at every row.
    The relative error is the maximum absolute deviation across the whole
    series divided by the maximum cumulative value reached on either side,
    so a local discrepancy that later cancels out still triggers the check.
    A single issue is raised per cumulative column whose error exceeds
    ``tolerance``. Columns missing from ``df`` are silently skipped.

    Parameters
    ----------
    df : DataFrame
        Time series data (pandas or polars).
    step_col : str, optional
        Name of the step identifier column.
    time_col : str, optional
        Name of the time column [s].
    current_col : str, optional
        Name of the signed current column [A].
    voltage_col : str, optional
        Name of the terminal voltage column [V]. Used to derive power as
        ``V * I`` when ``power_col`` is not present in ``df``.
    power_col : str, optional
        Name of the signed power column [W]. When absent, power is
        computed from ``voltage_col * current_col``.
    discharge_capacity_col, charge_capacity_col : str, optional
        Names of the cumulative capacity columns [A.h].
    discharge_energy_col, charge_energy_col : str, optional
        Names of the cumulative energy columns [W.h].
    tolerance : float, optional
        Maximum allowed relative error between the integrated and reported
        series, evaluated as ``max|reported - integrated| / max(reported,
        integrated)`` across all rows. Defaults to ``0.10`` (10 %).

    Returns
    -------
    list[ValidationIssue]
        One issue per cumulative column whose running series deviates from
        the integrated series by more than ``tolerance`` at any row. Empty
        when all present columns agree within tolerance everywhere.
    """
    if not _has_column(df, time_col) or not _has_column(df, step_col):
        return []

    time = np.asarray(_get_column(df, time_col), dtype=float)
    if len(time) < 2:
        return []
    step_data = _get_column(df, step_col)

    current = (
        np.asarray(_get_column(df, current_col), dtype=float)
        if _has_column(df, current_col)
        else None
    )
    if _has_column(df, power_col):
        power = np.asarray(_get_column(df, power_col), dtype=float)
        power_source = power_col
    elif current is not None and _has_column(df, voltage_col):
        # Power isn't reported separately — derive from V * I, which is
        # what the platform's conventional Power [W] column equals anyway.
        power = np.asarray(_get_column(df, voltage_col), dtype=float) * current
        power_source = f"{voltage_col} * {current_col}"
    else:
        power = None
        power_source = ""  # unused: the spec is filtered out when power is None

    specs: list[tuple[str, np.ndarray | None, str, str, str, int, IssueCode]] = [
        # (reported column, source array, source label for the message/payload,
        #  unit, source expression, sign-of-positive-half, issue code).
        # sign=+1 picks max(x, 0), sign=-1 picks max(-x, 0).
        (
            discharge_capacity_col,
            current,
            current_col,
            "A.h",
            "max(I, 0)",
            +1,
            IssueCode.DISCHARGE_CAPACITY_INTEGRAL_MISMATCH,
        ),
        (
            charge_capacity_col,
            current,
            current_col,
            "A.h",
            "max(-I, 0)",
            -1,
            IssueCode.CHARGE_CAPACITY_INTEGRAL_MISMATCH,
        ),
        (
            discharge_energy_col,
            power,
            power_source,
            "W.h",
            "max(P, 0)",
            +1,
            IssueCode.DISCHARGE_ENERGY_INTEGRAL_MISMATCH,
        ),
        (
            charge_energy_col,
            power,
            power_source,
            "W.h",
            "max(-P, 0)",
            -1,
            IssueCode.CHARGE_ENERGY_INTEGRAL_MISMATCH,
        ),
    ]
    active = [
        spec for spec in specs if _has_column(df, spec[0]) and spec[1] is not None
    ]
    if not active:
        return []

    boundary_idx = step_boundary_idx(step_data)

    errors: list[ValidationIssue] = []
    for (
        reported_col,
        source_array,
        source_col,
        unit,
        source_expr,
        sign,
        code,
    ) in active:
        signed = np.maximum(sign * source_array, 0.0)
        reported = np.asarray(_get_column(df, reported_col), dtype=float)
        integrated = running_step_reset_integral(
            signed, time, boundary_idx=boundary_idx
        )
        worst_idx, relative_error, max_scale = worst_row_relative_error(
            reported, integrated
        )
        if not np.isfinite(relative_error) or relative_error <= tolerance:
            continue
        errors.append(
            ValidationIssue(
                code,
                f"Column '{reported_col}' disagrees with the running integral "
                f"of '{source_expr}' over time by up to "
                f"{relative_error * 100:.1f}% (exceeds {tolerance * 100:.0f}% "
                f"tolerance). Worst row {worst_idx}: reported = "
                f"{float(reported[worst_idx]):.4f} {unit}, integrated = "
                f"{float(integrated[worst_idx]):.4f} {unit}.",
                payload={
                    "column": reported_col,
                    "source_column": source_col,
                    "unit": unit,
                    "worst_row_index": worst_idx,
                    "reported_at_worst": float(reported[worst_idx]),
                    "integrated_at_worst": float(integrated[worst_idx]),
                    "max_scale": max_scale,
                    "relative_error": relative_error,
                    "tolerance": tolerance,
                },
            )
        )

    return errors


STRICT_CHECK_NAMES: frozenset[str] = frozenset(
    {
        "minimum_points_per_step",
        "cycle_constant_within_step",
        "time_gaps",
        "voltage_continuity",
        "consecutive_same_direction_full_steps",
        "step_capacity_within_rated",
        "capacity_energy_from_current_power",
    }
)


def validate_measurement_data(
    df: DataFrame,
    strict: bool = False,
    data_type: str | None = None,
    steps_df: DataFrame | None = None,
    rated_capacity: float | None = None,
    voltage_window: tuple[float, float] | None = None,
    skip_checks: Iterable[str] | None = None,
) -> None:
    """Validate measurement time series data before upload.

    For standard cycler data (``data_type=None``), always runs:

    1. Positive current should correspond to discharge (voltage decreases)
    2. Time starts at 0
    3. Time is monotonically non-decreasing
    4. 'Step count' column exists, starts at 0, and increases by 1
    5. Cumulative values (capacity, energy) reset at each step start and
       only increase within steps

    The remaining checks are strict-mode only (``strict=True``):

    6. Each step has at least 2 data points
    7. Cycle number does not change within a step
    8. No time gap between consecutive rows exceeds 5 hours
    9. When ``voltage_window`` is provided, voltage is continuous between
       consecutive rows (no systematic chronological reordering)
    10. When ``steps_df`` and ``rated_capacity`` are provided, two
        consecutive steps delivering more than 2× rated capacity do not
        share the same direction
    11. When ``steps_df`` and ``rated_capacity`` are provided, no single
        step exceeds 500 % of the rated capacity (soft warning)
    12. Reported cumulative capacity/energy columns agree with the
        trapezoidal integral of current/power within 10 %

    For OCP data (``data_type="ocp"``), only validates:

    1. 'Voltage [V]' column exists
    2. 'Step count' column exists and is sequential

    Parameters
    ----------
    df : DataFrame
        Time series data to validate (pandas or polars DataFrame).
    strict : bool
        If False (default), run only the always-on checks above. If True,
        additionally run: minimum 2 points per step, cycle number constant
        within step, time-gap check, voltage-continuity check (when
        ``voltage_window`` is provided), and the step-capacity checks (when
        ``steps_df`` and ``rated_capacity`` are provided).
    data_type : str | None
        The type of data being validated. Use ``"ocp"`` for open-circuit
        potential data, which relaxes validation to skip current, time,
        capacity, and energy checks. Default is ``None`` (standard cycler
        data).
    steps_df : DataFrame | None
        Optional step summary dataframe. Used only in strict mode for the
        consecutive same-direction and per-step capacity checks.
    rated_capacity : float | None
        Rated (nominal) cell capacity in A.h. Used only in strict mode
        alongside ``steps_df`` to enable the consecutive-full-step and
        per-step capacity soft warnings.
    voltage_window : tuple[float, float] | None
        Rated ``(V_min, V_max)`` voltage window of the cell. Used only in
        strict mode to enable the voltage-continuity check.
    skip_checks : Iterable[str] | None
        Names of strict-mode checks to skip while keeping ``strict=True``
        for everything else. Use this to relax a single known-problematic
        check (least-privilege) instead of disabling strict mode entirely.
        Recognized names are listed in :data:`STRICT_CHECK_NAMES`:
        ``"minimum_points_per_step"``, ``"cycle_constant_within_step"``,
        ``"time_gaps"``, ``"voltage_continuity"``,
        ``"consecutive_same_direction_full_steps"``,
        ``"step_capacity_within_rated"``,
        ``"capacity_energy_from_current_power"``. Unknown names raise
        ``ValueError``.

    Raises
    ------
    MeasurementValidationError
        If any validation checks fail. The exception contains a list of all
        errors found.
    """
    skip = frozenset(skip_checks or ())
    unknown = skip - STRICT_CHECK_NAMES
    if unknown:
        raise ValueError(
            f"Unknown check name(s) in skip_checks: {sorted(unknown)}. "
            f"Valid names: {sorted(STRICT_CHECK_NAMES)}"
        )

    all_errors: list[ValidationIssue] = []
    step_col = "Step count"

    if data_type == "ocp":
        ocp_col_errors = validate_ocp_columns(df)
        all_errors.extend(ocp_col_errors)
        step_seq_errors = validate_step_count_sequential(df)
        all_errors.extend(step_seq_errors)
    else:
        all_errors.extend(validate_positive_current_is_discharge(df, step_col=step_col))
        all_errors.extend(validate_time_starts_at_zero(df))
        all_errors.extend(validate_time_monotonic(df))
        all_errors.extend(validate_step_count_sequential(df))

        if _has_column(df, step_col):
            all_errors.extend(validate_cumulative_values_reset_per_step(df, step_col))

        if strict:
            if _has_column(df, step_col):
                if "minimum_points_per_step" not in skip:
                    all_errors.extend(validate_minimum_points_per_step(df, step_col))
                if "cycle_constant_within_step" not in skip:
                    all_errors.extend(validate_cycle_constant_within_step(df, step_col))
                if "capacity_energy_from_current_power" not in skip:
                    all_errors.extend(
                        validate_capacity_energy_from_current_power(df, step_col)
                    )

            if "time_gaps" not in skip:
                all_errors.extend(validate_time_gaps(df))

            if voltage_window is not None and "voltage_continuity" not in skip:
                all_errors.extend(validate_voltage_continuity(df, voltage_window))

            if steps_df is not None and rated_capacity is not None:
                if "consecutive_same_direction_full_steps" not in skip:
                    all_errors.extend(
                        validate_consecutive_same_direction_full_steps(
                            steps_df, rated_capacity
                        )
                    )
                if "step_capacity_within_rated" not in skip:
                    for issue in validate_step_capacity_within_rated(
                        steps_df, rated_capacity
                    ):
                        warnings.warn(issue.message, stacklevel=2)

    if all_errors:
        raise MeasurementValidationError(
            f"Measurement data validation failed with {len(all_errors)} error(s):\n"
            + "\n".join(f"  - {err}" for err in all_errors),
            errors=all_errors,
        )


# --- Atomic validators ------------------------------------------------------ #


def df_to_dict_validator(v: Any) -> Any:
    """Convert DataFrame to dict with orient='list' for serialization."""
    if isinstance(v, pd.DataFrame):
        # Replace inf/-inf and NaN with None for JSON compatibility
        return v.replace([np.inf, -np.inf, np.nan], None).to_dict(orient="list")
    if isinstance(v, pl.DataFrame):
        # Replace inf/-inf and NaN with None for JSON compatibility
        # Process each column individually to avoid name conflicts
        result = {}
        for col_name in v.columns:
            col = v[col_name]
            if col.dtype.is_float():
                # Replace inf/-inf and NaN with None
                sanitized = col.to_list()
                sanitized = [
                    None if (x is not None and (math.isinf(x) or math.isnan(x))) else x
                    for x in sanitized
                ]
                result[col_name] = sanitized
            else:
                result[col_name] = col.to_list()
        return result
    return v


def dict_to_df_validator(v: Any, return_type: str | None = None) -> Any:
    """Convert dict to DataFrame for data processing.

    Parameters
    ----------
    v : Any
        Value to convert. If dict, converts to DataFrame.
    return_type : str | None
        Type of DataFrame to return: "polars" or "pandas".
        If None, uses the global setting from set_dataframe_backend().

    Returns
    -------
    Any
        DataFrame if input was dict, otherwise unchanged.
    """
    if isinstance(v, dict):
        backend = return_type if return_type is not None else _dataframe_backend
        # Check if all values are scalars (not lists/arrays)
        all_scalars = all(
            not isinstance(val, list | tuple | np.ndarray) for val in v.values()
        )
        if backend == "pandas":
            if all_scalars:
                return pd.DataFrame(v, index=[0])
            return pd.DataFrame(v)
        if all_scalars:
            return pl.DataFrame({k: [val] for k, val in v.items()})
        return pl.DataFrame(v)
    return v


def parameter_validator(v: Any) -> Any:
    """Convert pybamm.Symbol values to JSON-serializable form."""
    import pybamm

    if not isinstance(v, pybamm.Symbol):
        return v
    from pybamm.expression_tree.operations.serialise import convert_symbol_to_json

    return convert_symbol_to_json(v)


def float_sanitizer(v: Any) -> Any:
    """Sanitize float values to JSON-compatible forms.

    Converts inf, -inf, and NaN to None since these are not JSON-compliant.
    """
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    if isinstance(v, np.floating) and (np.isinf(v) or np.isnan(v)):
        return None
    return v


def bounds_tuple_validator(v: Any) -> Any:
    """Convert bounds 2-tuple to list for JSON serialization.

    Parameters
    ----------
    v : Any
        Value to validate. If it's a tuple with 2 elements, converts to list.

    Returns
    -------
    Any
        List if input was a 2-tuple, otherwise unchanged.
    """
    if isinstance(v, tuple) and len(v) == 2:
        return list(v)
    return v


def file_scheme_validator(v: Any) -> Any:
    """Convert file:// and folder:// scheme paths to serialized dicts.

    Handles ``file:`` prefixed paths (loads CSV as dict) and ``folder:``
    prefixed paths (loads time_series and steps as dict). For ``folder:``,
    parquet files are preferred over CSV when both are present.
    All other values are returned unchanged.

    Raises
    ------
    FileNotFoundError
        If the file or folder path doesn't exist.
    """
    if isinstance(v, str) and v.startswith("file:"):
        path = pathlib.Path(v.split(":")[1]).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"CSV file not found: {v}")
        return df_to_dict_validator(pd.read_csv(path))
    if isinstance(v, str) and v.startswith("folder:"):
        path = pathlib.Path(v.split(":")[1]).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"Folder not found: {v}")

        def _read(stem: str) -> Any:
            if (path / f"{stem}.parquet").exists():
                return df_to_dict_validator(pd.read_parquet(path / f"{stem}.parquet"))
            return df_to_dict_validator(pd.read_csv(path / f"{stem}.csv"))

        return {"time_series": _read("time_series"), "steps": _read("steps")}
    return v


# --- Pipeline composition helpers ------------------------------------------ #

Validator = Callable[[Any], Any]


def _apply_pipeline(value: Any, validators: Iterable[Validator]) -> Any:
    transformed = value
    for validator in validators:
        transformed = validator(transformed)
    return transformed


def pybamm_model_validator(v: Any) -> Any:
    """Convert pybamm.BaseModel instances to JSON-serializable config dicts."""
    import pybamm

    if not isinstance(v, pybamm.BaseModel):
        return v

    # Forward compat: PyBaMM PR #5411 adds to_config()
    if hasattr(v, "to_config"):
        return v.to_config()

    class_name = v.__class__.__name__
    # Built-in pybamm.lithium_ion models → lightweight config
    if hasattr(pybamm.lithium_ion, class_name):
        config: dict[str, Any] = {"type": class_name}
        if hasattr(v, "options"):
            opts = {k: val for k, val in v.options.items() if val is not None}
            if opts:
                config["options"] = opts
        return config

    # Custom / iwp models → full serialization + strip inf bounds
    from pybamm.expression_tree.operations.serialise import Serialise

    serialized = Serialise.serialise_custom_model(v)
    stripped = _strip_inf_bounds_from_serialized_model(serialized)
    return {"type": "custom", "model": stripped}


def _strip_inf_bounds_from_serialized_model(value: dict) -> dict:
    """Remove variable bounds containing inf/-inf from a serialized PyBaMM model.

    PyBaMM serializes variable bounds as ``[{value: -inf}, {value: inf}]``,
    which are not JSON compliant.  Dropping the ``bounds`` key entirely is
    safe because PyBaMM reconstructs default ``(-inf, inf)`` bounds during
    deserialization when the key is absent.
    """

    def _has_inf(bounds: list) -> bool:
        return any(
            isinstance(b, dict)
            and isinstance(b.get("value"), float | np.floating)
            and math.isinf(float(b["value"]))
            for b in bounds
        )

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: _walk(v)
                for k, v in obj.items()
                if not (k == "bounds" and isinstance(v, list) and _has_inf(v))
            }
        if isinstance(obj, list | tuple):
            return [_walk(item) for item in obj]
        return obj

    return _walk(value)


def _apply_recursive(value: Any, validators: Iterable[Validator]) -> Any:
    if isinstance(value, dict):
        # Sanitize serialized PyBaMM model dicts by stripping inf/-inf
        # values that are not JSON compliant. PyBaMM reconstructs default
        # bounds on deserialization so this is lossless.
        if "pybamm_version" in value and "schema_version" in value:
            return _strip_inf_bounds_from_serialized_model(value)
        return {key: _apply_recursive(val, validators) for key, val in value.items()}
    if isinstance(value, tuple):
        # Apply validators to tuple first (e.g., to convert bounds tuples to lists)
        transformed = _apply_pipeline(value, validators)
        # If validator converted tuple to list, process recursively
        if isinstance(transformed, list):
            return [_apply_recursive(item, validators) for item in transformed]
        # Otherwise, process tuple items recursively
        return [_apply_recursive(item, validators) for item in transformed]
    if isinstance(value, list):
        return [_apply_recursive(item, validators) for item in value]
    return _apply_pipeline(value, validators)


# --- Public pipelines ------------------------------------------------------- #


def _time_series_row_count_validator(v: Any) -> Any:
    """Block inline DataFrames that exceed the row limit.

    Raises
    ------
    MeasurementValidationError
        If the DataFrame has more than 1000 rows.
    """
    if isinstance(v, pd.DataFrame | pl.DataFrame):
        errors = validate_time_series_row_count(v)
        if errors:
            raise MeasurementValidationError(
                errors[0].message,
                errors=errors,
            )
    return v


validators_outbound: list[Validator] = [
    pybamm_model_validator,
    float_sanitizer,
    bounds_tuple_validator,
    file_scheme_validator,
    _time_series_row_count_validator,
    df_to_dict_validator,
    parameter_validator,
]

validators_inbound: list[Validator] = [
    dict_to_df_validator,
]


def run_validators_outbound(v: Any) -> Any:
    """Recursively apply outbound validators to values and nested containers."""
    return _apply_recursive(v, validators_outbound)


def run_validators_inbound(v: Any) -> Any:
    """Recursively apply inbound validators to values and nested containers."""
    return _apply_recursive(v, validators_inbound)
