"""Client-side normalization for the ``simulation_settings`` payload field."""

from __future__ import annotations

from typing import Any


def normalize_simulation_settings(
    data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Serialize a ``SimulationSettings`` object in a create/update payload.

    Lets callers pass a live ``ionworks_schema.models.SimulationSettings`` object
    as ``data["simulation_settings"]`` instead of pre-serializing it: when the
    value is such an instance it is replaced with its plain config dict via
    ``to_config()``. Any other value (a config ``dict``, ``None``, or absent) is
    passed through unchanged. The caller's dict is never mutated — a shallow copy
    is returned only when a conversion happens.

    Parameters
    ----------
    data : dict[str, Any] or None
        The create/update payload about to be sent to the backend.

    Returns
    -------
    dict[str, Any] or None
        The payload with ``simulation_settings`` serialized to a config dict when
        it was a ``SimulationSettings`` instance; otherwise ``data`` unchanged.
    """
    if not data or "simulation_settings" not in data:
        return data
    from ionworks_schema.models import SimulationSettings

    value = data["simulation_settings"]
    if isinstance(value, SimulationSettings):
        return {**data, "simulation_settings": value.to_config()}
    return data
