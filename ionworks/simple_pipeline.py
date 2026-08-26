"""
SimplePipeline client for fire-and-forget single-datafit pipeline runs.

This module provides the :class:`SimplePipelineClient` for creating and managing
SimplePipelines — a lightweight pipeline endpoint restricted to at most one
datafit or validation element. Unlike the full pipeline API, configs are
submitted as a single payload and executed end-to-end as one job.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from ._project_id import resolve_project_id
from .errors import IonworksError
from .models import PaginatedList, _build_endpoint, _parse_list_response
from .validators import run_validators_outbound

if TYPE_CHECKING:
    import ionworks_schema as iws

_TERMINAL_STATUSES = ("completed", "failed", "canceled")


def _coerce_simple_pipeline_to_dict(
    config: iws.SimplePipeline | dict[str, Any],
) -> dict[str, Any]:
    """Return a serialised config dict, accepting an iws pipeline or a dict.

    An ``iws.Pipeline`` / ``iws.SimplePipeline`` instance is serialised via
    ``to_config()`` (so it matches ``PipelineClient.create``, which already
    accepts schema instances); a dict is returned unchanged, its shape validated
    server-side against the endpoint's own SimplePipeline constraint.

    ``ionworks_schema`` is imported lazily so simply ``import ionworks`` doesn't
    drag in the schema package and its pybamm dependency.
    """
    if isinstance(config, dict):
        return config
    import ionworks_schema as iws

    if isinstance(config, iws.Pipeline):
        return config.to_config()
    raise TypeError(
        "SimplePipelineClient.create expects an ionworks_schema.SimplePipeline "
        f"or a dict, got {type(config).__name__}"
    )


class SimplePipelineSubmissionResponse(BaseModel):
    """Response from creating, getting, listing, or cancelling a SimplePipeline."""

    id: str
    project_id: str
    organization_id: str
    status: str
    created_at: str
    updated_at: str
    user_id: str | None = None
    name: str | None = None
    description: str | None = None
    created_by_email: str | None = None
    job_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    config_path: str | None = None
    result: dict[str, Any] | None = None


class SimplePipelineClient:
    """Client for creating and managing SimplePipeline runs.

    SimplePipeline is headless: a run is not shown in the web app and no
    model is saved from it. Fitted parameters are returned to the caller
    through :meth:`get` / :meth:`wait_for_completion`. Use
    ``client.pipeline`` instead when the run should be visible there.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the SimplePipeline client.

        Parameters
        ----------
        client : Any
            The HTTP client to use for API requests.
        """
        self.client = client

    def create(
        self,
        config: iws.SimplePipeline | dict[str, Any],
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SimplePipelineSubmissionResponse:
        """Create and submit a SimplePipeline for execution.

        Parameters
        ----------
        config : iws.SimplePipeline | dict[str, Any]
            An ``ionworks_schema.SimplePipeline`` instance, or a raw config dict
            with an ``elements`` dict. May contain at most one ``data_fit`` or
            ``validation`` element.
        project_id : str | None, optional
            Project ID to associate the pipeline with. Falls back to the project_id
            on the Ionworks client (resolved from ``IONWORKS_PROJECT_ID``).
        name : str | None, optional
            Optional human-readable name for the pipeline.
        description : str | None, optional
            Optional human-readable description for the pipeline.
        options : dict[str, Any], optional
            Submission options (e.g. ``{"live_progress_updates": True}``).
            Falls back to ``config["options"]`` (dict form).

        Returns
        -------
        SimplePipelineSubmissionResponse
            The created SimplePipeline record (status starts as ``pending``).

        Raises
        ------
        ValueError
            If ``project_id`` is not provided and not set on the client.
        """
        config_copy = dict(_coerce_simple_pipeline_to_dict(config))
        project_id = (
            project_id
            if project_id is not None
            else config_copy.pop("project_id", None)
        )
        options = options if options is not None else config_copy.pop("options", None)
        name = name if name is not None else config_copy.pop("name", None)
        description = (
            description
            if description is not None
            else config_copy.pop("description", None)
        )

        resolved_project_id = resolve_project_id(self.client, project_id)
        serialized_config = run_validators_outbound(config_copy)

        payload: dict[str, Any] = {
            "project_id": resolved_project_id,
            "config": serialized_config,
        }
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if options is not None:
            payload["options"] = options

        response_data = self.client.post("/simple_pipelines", payload)
        try:
            return SimplePipelineSubmissionResponse(**response_data)
        except ValidationError as e:
            raise ValueError(f"Invalid response from /simple_pipelines: {e}") from e

    def update(
        self,
        simple_pipeline_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> SimplePipelineSubmissionResponse:
        """Partially update a SimplePipeline's name and/or description.

        Parameters
        ----------
        simple_pipeline_id : str
            The SimplePipeline ID to update.
        name : str | None, optional
            New name. Omit (or pass ``None``) to leave unchanged.
        description : str | None, optional
            New description. Omit (or pass ``None``) to leave unchanged.

        Returns
        -------
        SimplePipelineSubmissionResponse
            The updated record.

        Raises
        ------
        ValueError
            If neither ``name`` nor ``description`` is provided.
        """
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if not payload:
            raise ValueError(
                "At least one of 'name' or 'description' must be provided."
            )

        response_data = self.client.patch(
            f"/simple_pipelines/{simple_pipeline_id}", payload
        )
        return SimplePipelineSubmissionResponse(**response_data)

    def get(self, simple_pipeline_id: str) -> SimplePipelineSubmissionResponse:
        """Fetch a SimplePipeline by ID.

        Parameters
        ----------
        simple_pipeline_id : str
            The SimplePipeline ID returned by :meth:`create`.

        Returns
        -------
        SimplePipelineSubmissionResponse
            The current record, including ``status`` and (if completed) ``result``.
        """
        response_data = self.client.get(f"/simple_pipelines/{simple_pipeline_id}")
        return SimplePipelineSubmissionResponse(**response_data)

    def list(
        self,
        project_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        created_by_email: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        created_at_gt: str | None = None,
        created_at_lt: str | None = None,
        updated_at_gt: str | None = None,
        updated_at_lt: str | None = None,
        order_by: str = "created_at",
        order: str = "desc",
    ) -> PaginatedList[SimplePipelineSubmissionResponse]:
        """List SimplePipelines for a project.

        Parameters
        ----------
        project_id : str | None, optional
            Project ID to filter by. Falls back to the project_id on the
            Ionworks client (resolved from ``IONWORKS_PROJECT_ID``).
        limit : int, optional
            Maximum items per page. Defaults to 10. Server-enforced max is 100.
        offset : int, optional
            Number of items to skip for pagination. Defaults to 0.
        name, description, status, created_by_email : str | None, optional
            Optional filters. Pass values directly for exact match or use
            Supabase operator syntax (e.g. ``"ilike.%foo%"``,
            ``"in.(completed,failed)"``).
        created_at, updated_at : str | None, optional
            Optional Supabase-operator filter on the timestamp field
            (e.g. ``"gte.2025-01-01"``).
        created_at_gt, created_at_lt, updated_at_gt, updated_at_lt :
            str | None, optional
            Inclusive lower / upper bounds for between-style date queries.
        order_by : str, optional
            Sort column. One of ``id``, ``name``, ``description``, ``status``,
            ``created_at``, ``updated_at``, ``created_by_email``. Defaults to
            ``"created_at"``.
        order : str, optional
            Sort direction (``"asc"`` or ``"desc"``). Defaults to ``"desc"``.

        Returns
        -------
        PaginatedList[SimplePipelineSubmissionResponse]
            A list-like page of SimplePipeline records, with ``.count`` and
            ``.total`` for pagination.

        Raises
        ------
        ValueError
            If ``project_id`` is not provided and not set on the client.
        """
        resolved_project_id = resolve_project_id(self.client, project_id)

        endpoint = _build_endpoint(
            "/simple_pipelines",
            {
                "project_id": resolved_project_id,
                "limit": limit,
                "offset": offset,
                "order_by": order_by,
                "order": order,
                "name": name,
                "description": description,
                "status": status,
                "created_by_email": created_by_email,
                "created_at": created_at,
                "updated_at": updated_at,
                "created_at_gt": created_at_gt,
                "created_at_lt": created_at_lt,
                "updated_at_gt": updated_at_gt,
                "updated_at_lt": updated_at_lt,
            },
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, SimplePipelineSubmissionResponse)

    def cancel(self, simple_pipeline_id: str) -> SimplePipelineSubmissionResponse:
        """Cancel a running SimplePipeline.

        Parameters
        ----------
        simple_pipeline_id : str
            The SimplePipeline ID to cancel.

        Returns
        -------
        SimplePipelineSubmissionResponse
            The updated record (status will be ``canceled`` if cancellation
            took effect; otherwise the current state is returned).
        """
        response_data = self.client.post(
            f"/simple_pipelines/{simple_pipeline_id}/cancel", {}
        )
        return SimplePipelineSubmissionResponse(**response_data)

    def delete(self, simple_pipeline_id: str) -> None:
        """Delete a SimplePipeline and its associated job.

        Parameters
        ----------
        simple_pipeline_id : str
            The SimplePipeline ID to delete.
        """
        self.client.delete(f"/simple_pipelines/{simple_pipeline_id}")

    def wait_for_completion(
        self,
        simple_pipeline_id: str,
        timeout: int = 600,
        poll_interval: int = 2,
        verbose: bool = True,
        raise_on_failure: bool = True,
    ) -> SimplePipelineSubmissionResponse:
        """Poll a SimplePipeline until it reaches a terminal state or times out.

        Parameters
        ----------
        simple_pipeline_id : str
            The SimplePipeline ID to wait for.
        timeout : int, optional
            Maximum time to wait in seconds. Defaults to 600.
        poll_interval : int, optional
            Time between polls in seconds. Defaults to 2.
        verbose : bool, optional
            Print status updates while polling. Defaults to True.
        raise_on_failure : bool, optional
            Raise :class:`IonworksError` when the pipeline ends in ``failed``.
            Defaults to True.

        Returns
        -------
        SimplePipelineSubmissionResponse
            The terminal record.

        Raises
        ------
        TimeoutError
            If the pipeline does not reach a terminal state within ``timeout``.
        IonworksError
            If the pipeline failed and ``raise_on_failure`` is True.
        """
        deadline = time.time() + timeout
        pipeline = self.get(simple_pipeline_id)

        if verbose:
            print(f"Polling simple_pipeline {simple_pipeline_id} for completion...")

        while pipeline.status not in _TERMINAL_STATUSES:
            if time.time() >= deadline:
                raise TimeoutError(
                    f"SimplePipeline {simple_pipeline_id} did not complete within "
                    f"{timeout} seconds (status: {pipeline.status})"
                )
            time.sleep(poll_interval)
            pipeline = self.get(simple_pipeline_id)
            if verbose:
                elapsed = int(timeout - (deadline - time.time()))
                print(f"  Status: {pipeline.status} (elapsed: {elapsed}s)")

        if verbose:
            print(f"SimplePipeline finished with status: {pipeline.status}")
            if pipeline.status == "failed" and pipeline.error:
                print(f"  Error: {pipeline.error}")

        if pipeline.status == "failed" and raise_on_failure:
            error_msg = f"SimplePipeline {simple_pipeline_id} failed"
            if pipeline.error:
                error_msg += f": {pipeline.error}"
            raise IonworksError(error_msg)

        return pipeline
