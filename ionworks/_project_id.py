"""Shared helpers for resolving a default project_id across sub-clients.

Sub-clients call :func:`resolve_project_id` to fall back to the project_id
configured on the parent :class:`~ionworks.Ionworks` client when callers
omit it. The client itself resolves the env var (``IONWORKS_PROJECT_ID``,
with the deprecated ``PROJECT_ID`` as a fallback) at construction time, so
sub-clients only need to consult ``client.project_id``.
"""

from __future__ import annotations

import os
from typing import Any
import warnings


def resolve_env_project_id() -> str | None:
    """Resolve project_id from environment variables.

    Prefers ``IONWORKS_PROJECT_ID``; falls back to the deprecated
    ``PROJECT_ID`` with a :class:`DeprecationWarning`. Returns ``None`` if
    neither is set.
    """
    project_id = os.getenv("IONWORKS_PROJECT_ID")
    if project_id is not None:
        return project_id
    legacy = os.getenv("PROJECT_ID")
    if legacy is not None:
        warnings.warn(
            "The PROJECT_ID environment variable is deprecated and will be "
            "removed in a future release. Use IONWORKS_PROJECT_ID instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        return legacy
    return None


def resolve_project_id(
    client: Any,
    project_id: str | None,
    *,
    required: bool = True,
) -> str | None:
    """Resolve project_id from explicit arg or the parent client default.

    Parameters
    ----------
    client : Any
        The parent Ionworks client (must have a ``project_id`` attribute,
        which may be ``None``).
    project_id : str | None
        The explicit project_id passed by the caller, if any.
    required : bool, optional
        If True (default), raise :class:`ValueError` when no project_id is
        available from either source. If False, return ``None`` instead.

    Returns
    -------
    str | None
        The resolved project_id, or ``None`` if not required and not found.

    Raises
    ------
    ValueError
        If ``required`` is True and no project_id can be resolved.
    """
    resolved = project_id or getattr(client, "project_id", None)
    if not resolved and required:
        raise ValueError(
            "project_id is required. Pass it explicitly, set it on the "
            "Ionworks client, or set the IONWORKS_PROJECT_ID environment "
            "variable."
        )
    return resolved


def inject_project_id(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``payload`` with ``project_id`` injected from the client default.

    If the payload already has a non-None ``project_id``, it is returned
    unchanged. Otherwise, if the client has a default ``project_id``, a new
    dict is returned with that value set.
    """
    if payload.get("project_id") is None and getattr(client, "project_id", None):
        return {**payload, "project_id": client.project_id}
    return payload
