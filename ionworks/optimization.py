"""Optimization client for managing design optimizations.

This module provides the :class:`OptimizationClient` for running, monitoring,
and managing battery design optimization jobs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ._project_id import inject_project_id, resolve_project_id
from .errors import IonworksError
from .models import (
    Optimization,
    _build_endpoint,
)

_logger = logging.getLogger(__name__)


class OptimizationClient:
    """Client for managing design optimizations.

    Optimizations run parameter sweeps and objective-driven searches over
    parameterized battery models.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the OptimizationClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def run(self, data: dict[str, Any]) -> Optimization:
        """Submit a new optimization job.

        Parameters
        ----------
        data : dict[str, Any]
            The optimization configuration. See backend documentation for the
            full schema (varies by optimization type: design optimization,
            data fit optimization, etc.). Typically includes ``name``,
            ``project_id``, ``parameterized_model_id``, and type-specific
            config.

        Returns
        -------
        Optimization
            The newly created optimization record (includes ``job_id``).
        """
        data = inject_project_id(self.client, data)
        response_data = self.client.post("/optimizations", data)
        return Optimization(**response_data)

    def get(self, optimization_id: str) -> dict[str, Any]:
        """Get an optimization resource by id.

        Parameters
        ----------
        optimization_id : str
            The ID of the optimization to retrieve.

        Returns
        -------
        dict[str, Any]
            The optimization resource. Includes the lifecycle ``status``
            (one of ``queued``, ``running``, ``succeeded``, ``failed``,
            ``canceled``), result ``metrics``, ``error``, names, and counts.
        """
        return self.client.get(f"/optimizations/{optimization_id}")

    def list(
        self,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """List optimizations with optional filtering.

        Parameters
        ----------
        project_id : str | None, optional
            Filter by project ID. If not provided, uses the project_id set
            on the Ionworks client (resolved from the ``IONWORKS_PROJECT_ID``
            env var if not passed to the client).
        limit : int | None, optional
            Maximum number of optimizations to return.
        offset : int | None, optional
            Number of optimizations to skip for pagination.

        Returns
        -------
        dict[str, Any]
            Dictionary with ``optimizations`` (job-free resources) and
            ``total`` keys.
        """
        project_id = resolve_project_id(self.client, project_id, required=False)
        endpoint = _build_endpoint(
            "/optimizations",
            {
                "project_id": project_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return self.client.get(endpoint)

    def update(self, optimization_id: str, data: dict[str, Any]) -> Optimization:
        """Update an optimization's name and/or description.

        Parameters
        ----------
        optimization_id : str
            The ID of the optimization to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports ``name`` and
            ``description``.

        Returns
        -------
        Optimization
            The updated optimization object.
        """
        response_data = self.client.patch(f"/optimizations/{optimization_id}", data)
        return Optimization(**response_data)

    def cancel(self, optimization_id: str) -> dict[str, Any]:
        """Cancel a running optimization.

        Parameters
        ----------
        optimization_id : str
            The ID of the optimization to cancel.

        Returns
        -------
        dict[str, Any]
            The optimization resource with its updated status.
        """
        return self.client.post(f"/optimizations/{optimization_id}/cancel", {})

    def wait_for_completion(
        self,
        optimization_id: str,
        timeout: int = 600,
        poll_interval: int = 3,
        verbose: bool = True,
        raise_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Poll an optimization until it completes, fails, or times out.

        Parameters
        ----------
        optimization_id : str
            The ID of the optimization to wait for.
        timeout : int, optional
            Maximum time to wait in seconds. Defaults to 600.
        poll_interval : int, optional
            Time between polls in seconds. Defaults to 3.
        verbose : bool, optional
            If True, print status updates. Defaults to True.
        raise_on_failure : bool, optional
            If True, raise an error when the optimization fails. Defaults to
            True.

        Returns
        -------
        dict[str, Any]
            The final optimization resource (including its ``status``).

        Raises
        ------
        TimeoutError
            If the optimization does not complete within the timeout.
        IonworksError
            If the optimization fails and ``raise_on_failure`` is True.
        """
        deadline = time.time() + timeout
        terminal_states = {"succeeded", "failed", "canceled"}

        while True:
            result = self.get(optimization_id)
            status = result.get("status")

            if verbose:
                _logger.info(
                    "Optimization %s... status: %s", optimization_id[:12], status
                )

            if status in terminal_states:
                if status != "succeeded" and raise_on_failure:
                    error_msg = result.get("error", "Unknown error")
                    raise IonworksError(
                        {
                            "error_code": "JOB_FAILED",
                            "message": (
                                f"Optimization {optimization_id} {status}: {error_msg}"
                            ),
                            "detail": {
                                "status": status,
                                "optimization_id": optimization_id,
                            },
                        }
                    )
                return result

            if time.time() >= deadline:
                break

            time.sleep(poll_interval)

        raise TimeoutError(
            f"Optimization {optimization_id} did not complete within {timeout} seconds"
        )
