"""Client-side normalization for the ``parameters`` payload field."""

from __future__ import annotations

from typing import Any


def normalize_parameters(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a ``pybamm.ParameterValues`` or parameter-set name in a payload.

    Lets callers pass ``data["parameters"]`` as a live ``pybamm.ParameterValues``
    or the name of a pybamm parameter set (e.g. ``"Chen2020"``) instead of
    pre-serializing it; either is replaced with ``ParameterValues.to_json()``. Any
    other value is passed through unchanged. The caller's dict is never mutated —
    a shallow copy is returned only when a conversion happens.

    Parameters
    ----------
    data : dict[str, Any] or None
        The create payload about to be sent to the backend.

    Returns
    -------
    dict[str, Any] or None
        The payload with ``parameters`` serialized when it was a
        ``ParameterValues`` or a parameter-set name; otherwise ``data`` unchanged.
    """
    if not data or "parameters" not in data:
        return data
    value = data["parameters"]
    if isinstance(value, str):
        import pybamm

        value = pybamm.ParameterValues(value)
    if hasattr(value, "to_json"):
        return {**data, "parameters": value.to_json()}
    return data
