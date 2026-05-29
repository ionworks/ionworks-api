"""
Pipeline client for running parameterization workflows.

This module provides the :class:`PipelineClient` for creating and managing
pipelines that combine data fitting, calculations, and validation steps
for battery model parameterization.

Pipeline shape validation is delegated to :mod:`ionworks_schema` — both
this client's :meth:`PipelineClient.create` method and the backend route
parse against the same schema, so a payload that builds with
``iws.Pipeline(...)`` validates identically end-to-end.
"""

import re
import time
from typing import TYPE_CHECKING, Any

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    model_validator,
)

from ._project_id import resolve_env_project_id, resolve_project_id
from .errors import IonworksError
from .validators import run_validators_outbound

if TYPE_CHECKING:
    import ionworks_schema as iws


def _prepare_payload(data: Any) -> Any:
    """Prepare payload for API submission using outbound validators pipeline."""
    return run_validators_outbound(data)


def _coerce_pipeline_to_dict(config: "iws.Pipeline | dict[str, Any]") -> dict[str, Any]:
    """Validate ``config`` against ``iws.Pipeline`` and return its serialised dict.

    Accepts either an ``iws.Pipeline`` schema instance (already validated by
    construction) or a raw dict. The dict path runs ``iws.Pipeline.model_validate``
    so unknown top-level fields and shape errors surface here, before submission.

    ``ionworks_schema`` is imported lazily so simply ``import ionworks`` (the
    SDK) doesn't drag in the schema package and its pybamm dependency — the
    SDK is used by callers (e.g. ``ionworksdata``) that maintain a strict
    no-pybamm-on-import contract.
    """
    import ionworks_schema as iws

    if isinstance(config, iws.Pipeline):
        return config.to_config()
    if not isinstance(config, dict):
        raise TypeError(
            "PipelineClient.create expects an ionworks_schema.Pipeline "
            f"or a dict, got {type(config).__name__}"
        )
    pipeline = iws.Pipeline.model_validate(config)
    return pipeline.to_config()


class PipelineSubmissionMetadata(BaseModel):
    """SDK-only metadata attached to a pipeline submission.

    These fields are not part of ``ionworks_schema.Pipeline`` because they
    describe *how* the submission is routed (which project, which
    runtime options) rather than *what* the pipeline does.
    """

    project_id: str | None = Field(
        default=None,
        description="The project id to submit the pipeline to. "
        "Can be found in the project settings page. "
        "If not provided, will use the project_id set on the Ionworks "
        "client or the IONWORKS_PROJECT_ID environment variable.",
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Pipeline runtime options (e.g. live_progress_updates: bool).",
    )

    @model_validator(mode="after")
    def resolve_project_id(self) -> "PipelineSubmissionMetadata":
        """Resolve project_id from env vars if not provided.

        Prefers ``IONWORKS_PROJECT_ID``; falls back to the deprecated
        ``PROJECT_ID`` (with a ``DeprecationWarning``) via
        :func:`resolve_env_project_id`.
        """
        if self.project_id is None:
            self.project_id = resolve_env_project_id()
            if self.project_id is None:
                raise ValueError(
                    "project_id is required. Pass it explicitly, set it on "
                    "the Ionworks client, or set the IONWORKS_PROJECT_ID "
                    "environment variable."
                )
        # Ensure options is never None to avoid 422 errors at the backend.
        if self.options is None:
            self.options = {}
        return self


class DataFitResponse(BaseModel):
    """Response from a data fitting step containing fitted parameters."""

    parameter_values: dict[str, Any]


class CalculationResponse(BaseModel):
    """Response from a calculation step containing calculated parameters."""

    parameter_values: dict[str, Any]


class ValidationResponse(BaseModel):
    """Response from a validation step containing validation results."""

    validation_results: dict[str, Any]
    summary_stats: dict[str, list[Any]]


class EntryResponse(BaseModel):
    """Response from an entry point containing parameter values."""

    parameter_values: dict[str, Any]


class PipelineSubmissionResponse(BaseModel):
    """Response from submitting a pipeline to the API."""

    id: str
    name: str
    description: str | None = None
    status: str
    error: str | None = None


class PipelineResponse(BaseModel):
    """Complete response from retrieving pipeline results."""

    result: dict[str, Any]
    element_results: dict[str, Any]


class PipelineClient:
    """Client for creating and managing pipeline workflows."""

    def __init__(self, client: Any) -> None:
        """Initialize the pipeline client.

        Parameters
        ----------
        client : Any
            The HTTP client to use for API requests.
        """
        self.client = client

    def create(
        self,
        config: "iws.Pipeline | dict[str, Any]",
        *,
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> PipelineSubmissionResponse:
        """Run a complete pipeline with the given configuration.

        Parameters
        ----------
        config : ionworks_schema.Pipeline or dict[str, Any]
            Pipeline configuration. Either an ``iws.Pipeline`` schema instance
            (constructed via ``iws.Pipeline(elements=...)``) or a dict with
            ``elements``, ``name``, ``description`` and SDK-only fields such as
            ``project_id``/``options``. Dicts are validated against
            ``iws.Pipeline`` before submission so shape errors surface locally.
        project_id : str, optional
            Project to submit to. Falls back to a ``project_id`` field on
            ``config`` (dict form), then to the ``PROJECT_ID`` env var.
        name : str, optional
            Submission name override. Falls back to the schema's ``name`` field.
        description : str, optional
            Submission description override. Falls back to the schema's
            ``description`` field.
        options : dict[str, Any], optional
            Submission options (e.g. ``{"live_progress_updates": True}``).
            Falls back to ``config["options"]`` (dict form).

        Returns
        -------
        PipelineSubmissionResponse
            The pipeline submission response.

        Raises
        ------
        ValueError
            If the configuration is invalid.
        """
        # Pull SDK-only fields out of dict form before passing to iws.Pipeline,
        # which has extra='forbid' and would reject project_id/options at the top level.
        if isinstance(config, dict):
            config_copy = dict(config)
            project_id = (
                project_id
                if project_id is not None
                else config_copy.pop("project_id", None)
            )
            options = (
                options if options is not None else config_copy.pop("options", None)
            )
            name = name if name is not None else config_copy.pop("name", None)
            description = (
                description
                if description is not None
                else config_copy.pop("description", None)
            )
            config_for_validate: iws.Pipeline | dict[str, Any] = config_copy
        else:
            config_for_validate = config
            # Schema instance carries name/description on itself; pull defaults.
            # Lazy-import iws to keep the SDK's import footprint pybamm-free.
            import ionworks_schema as _iws

            if isinstance(config, _iws.Pipeline):
                name = name if name is not None else config.name
                description = (
                    description if description is not None else config.description
                )

        try:
            # Coerce / validate the pipeline shape first so type errors and
            # schema-shape errors surface before metadata resolution.
            payload = _coerce_pipeline_to_dict(config_for_validate)
            # Fall back to the client's default project_id before the
            # metadata validator consults env vars — same precedence as
            # the legacy single-config flow used.
            project_id = resolve_project_id(self.client, project_id, required=False)
            metadata = PipelineSubmissionMetadata(
                project_id=project_id, options=options
            )
            payload["project_id"] = metadata.project_id
            payload["options"] = metadata.options
            if name is not None:
                payload["name"] = name
            if description is not None:
                payload["description"] = description
            payload = _prepare_payload(payload)
            response_data = self.client.post("/pipelines", payload)
            return PipelineSubmissionResponse(**response_data)
        except ValidationError as e:
            raise ValueError(f"Invalid pipeline configuration: {e}") from e
        except IonworksError as e:
            error_msg = str(e.message)
            # Check for invalid UUID format in project_id
            uuid_match = re.search(
                r'invalid input syntax for type uuid: "([^"]*)"', error_msg
            )
            if uuid_match:
                invalid_id = uuid_match.group(1)
                raise ValueError(
                    f"Invalid project_id format: '{invalid_id}' is not a valid UUID. "
                    "Please provide a valid project ID from your project settings page."
                ) from e
            # Check for permission denied (RLS violation or other access issue)
            if e.error_code == "FORBIDDEN" or e.status_code == 403:
                raise ValueError(
                    f"Access denied: The project '{metadata.project_id}' is not "
                    "accessible with your API key. Please verify that your API key "
                    "has access to this project."
                ) from e
            # Re-raise original error for other cases
            raise

    def list(
        self, project_id: str | None = None, limit: int | None = None
    ) -> list[PipelineSubmissionResponse]:
        """List all pipelines.

        Parameters
        ----------
        project_id : str | None
            The project id to filter pipelines. If not provided, uses the
            project_id set on the Ionworks client or the
            IONWORKS_PROJECT_ID environment variable.
        limit : int | None
            Maximum number of pipelines to return. If not provided, returns
            all pipelines (up to the API's default limit).

        Returns
        -------
        list[PipelineSubmissionResponse]
            List of pipeline submission responses.

        Raises
        ------
        ValueError
            If response data is not a list or project_id is missing.
        """
        # Fall back to client default; if neither, try env var directly.
        project_id = resolve_project_id(self.client, project_id, required=False)
        if project_id is None:
            project_id = resolve_env_project_id()
        if project_id is None:
            raise ValueError(
                "project_id is required. Pass it explicitly, set it on "
                "the Ionworks client, or set the IONWORKS_PROJECT_ID "
                "environment variable."
            )

        endpoint = f"/pipelines?project_id={project_id}"
        if limit is not None:
            endpoint += f"&limit={limit}"
        try:
            response_data = self.client.get(endpoint)
            # Handle both old list format and new paginated format
            if isinstance(response_data, dict) and "pipelines" in response_data:
                pipelines = response_data["pipelines"]
            elif isinstance(response_data, list):
                pipelines = response_data
            else:
                raise ValueError(
                    f"Unexpected response format from {endpoint}: expected a list or "
                    f"dict with 'pipelines' key, got {type(response_data).__name__}"
                )
            return [PipelineSubmissionResponse(**item) for item in pipelines]
        except ValidationError as e:
            raise ValueError(f"Invalid item format in list from {endpoint}: {e}") from e

    def get(self, job_id: str) -> PipelineSubmissionResponse:
        """Get the pipeline response for the given job id.

        Parameters
        ----------
        job_id : str
            The job id.

        Returns
        -------
        PipelineSubmissionResponse
            The pipeline submission response.
        """
        response_data = self.client.get(f"/pipelines/{job_id}")
        return PipelineSubmissionResponse(**response_data)

    def result(self, job_id: str) -> PipelineResponse:
        """Get the result for the given job id.

        Parameters
        ----------
        job_id : str
            The job id.

        Returns
        -------
        PipelineResponse
            The pipeline results.
        """
        response_data = self.client.get(f"/pipelines/{job_id}/result")
        return PipelineResponse(**response_data)

    def get_element_metadata(
        self,
        pipeline_id: str,
        element_name: str,
        elements: "list[dict[str, Any]] | None" = None,
    ) -> dict[str, Any]:
        """Fetch the metadata blob for a named element of a pipeline.

        Locates the element by name in the pipeline's elements list, then
        delegates to ``client.job.get_metadata`` for the underlying job. Use
        this when you need fields that are stripped from ``element.result``
        and persisted to storage instead — for example,
        ``validation_results`` and ``validation_plot_config`` written by a
        validation element.

        Parameters
        ----------
        pipeline_id : str
            The pipeline whose element metadata to fetch.
        element_name : str
            The name of the element within the pipeline — the key used in the
            ``elements`` dict at submission time. Element names are
            user-chosen and unique per pipeline (a pipeline may run multiple
            validation elements under names like ``"validate_pristine"`` and
            ``"validate_aged"``).
        elements : list[dict], optional
            Pre-fetched elements list from ``GET /pipelines/{id}/elements``.
            Pass this when pulling metadata for several elements of the same
            pipeline to avoid re-fetching the list on every call. When
            omitted, the list is fetched fresh.

        Returns
        -------
        dict[str, Any]
            The parsed metadata payload for the element's job.

        Raises
        ------
        ValueError
            If the pipeline has no element with the given name, or that
            element has no associated job (e.g. it never ran).
        """
        if elements is None:
            elements = self.client.get(f"/pipelines/{pipeline_id}/elements")
        for element in elements:
            if element.get("name") == element_name:
                job_id = element.get("job_id")
                if not job_id:
                    raise ValueError(
                        f"Element '{element_name}' in pipeline {pipeline_id} "
                        f"has no associated job — it may not have run yet."
                    )
                return self.client.job.get_metadata(job_id)
        raise ValueError(
            f"No element named '{element_name}' found in pipeline {pipeline_id}."
        )

    def wait_for_completion(
        self,
        pipeline_id: str,
        timeout: int = 600,
        poll_interval: int = 2,
        verbose: bool = True,
        raise_on_failure: bool = True,
    ) -> PipelineSubmissionResponse:
        """Wait for a pipeline to complete by polling until done or timeout.

        Parameters
        ----------
        pipeline_id : str
            The pipeline ID to wait for.
        timeout : int, optional
            Maximum time to wait in seconds (default: 600).
        poll_interval : int, optional
            Time between polls in seconds (default: 2).
        verbose : bool, optional
            Whether to print status updates (default: True).
        raise_on_failure : bool, optional
            Whether to raise IonworksError when pipeline fails (default: True).

        Returns
        -------
        PipelineSubmissionResponse
            The completed (or failed, if raise_on_failure=False) pipeline response.

        Raises
        ------
        TimeoutError
            If timeout is reached before the pipeline completes.
        IonworksError
            If the pipeline fails and raise_on_failure is True.
        """
        deadline = time.time() + timeout
        pipeline = self.get(pipeline_id)

        if verbose:
            print(f"Polling pipeline {pipeline_id} for completion...")

        while pipeline.status not in ("completed", "failed"):
            if time.time() >= deadline:
                raise TimeoutError(
                    f"Pipeline {pipeline_id} did not complete within "
                    f"{timeout} seconds (status: {pipeline.status})"
                )
            time.sleep(poll_interval)
            pipeline = self.get(pipeline_id)
            if verbose:
                elapsed = int(timeout - (deadline - time.time()))
                print(f"  Status: {pipeline.status} (elapsed: {elapsed}s)")

        if verbose:
            print(f"Pipeline finished with status: {pipeline.status}")
            if pipeline.status == "failed" and pipeline.error:
                print(f"  Error: {pipeline.error}")

        if pipeline.status == "failed" and raise_on_failure:
            error_msg = f"Pipeline {pipeline_id} failed"
            if pipeline.error:
                error_msg += f": {pipeline.error}"
            raise IonworksError(error_msg)

        return pipeline
