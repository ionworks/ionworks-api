"""Client-side normalization for the model ``config`` payload field."""

from __future__ import annotations

from typing import Any


def normalize_model_config(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a pybamm model passed as ``data['config']`` to a config dict.

    Lets callers pass a live built-in ``pybamm.BaseModel`` (e.g.
    ``pybamm.lithium_ion.SPMe()``) as the model ``config`` instead of a
    hand-written ``{"type": ...}`` dict — mirroring how ``ionworks_schema``
    accepts a pybamm model as the model in a fit. It is serialized to the same
    ``{"type": <class>, "options": {...}}`` form the pipeline's model parser
    reads, so a model created this way discretises identically. A ``dict`` config
    (or absent / ``None``) passes through unchanged.

    Custom (non-built-in) pybamm models are not accepted here — they carry
    serialized model data that belongs on the dedicated upload endpoint; use
    :meth:`ionworks.custom_model.ModelClient.upload_custom` for those.

    Parameters
    ----------
    data : dict[str, Any] or None
        The create/update payload about to be sent to the backend.

    Returns
    -------
    dict[str, Any] or None
        The payload with ``config`` serialized to a config dict when it was a
        ``pybamm.BaseModel``; otherwise ``data`` unchanged. A shallow copy is
        returned only when a conversion happens; the caller's dict is not mutated.

    Raises
    ------
    TypeError
        If ``config`` is a custom (non-built-in) pybamm model.
    """
    if not data or "config" not in data:
        return data
    config = data["config"]
    if config is None or isinstance(config, dict):
        return data

    import pybamm

    if isinstance(config, pybamm.BaseModel):
        class_name = type(config).__name__
        # Only built-in models serialize to a plain {"type": ...} config; custom
        # models carry serialized model data that belongs on upload_custom.
        if not hasattr(pybamm.lithium_ion, class_name):
            raise TypeError(
                f"{class_name} is a custom pybamm model; create it with "
                "ModelClient.upload_custom(model, name=...) instead of create()."
            )
        serialized: dict[str, Any] = {"type": class_name}
        options = {
            k: v for k, v in getattr(config, "options", {}).items() if v is not None
        }
        if options:
            serialized["options"] = options
        return {**data, "config": serialized}
    return data
