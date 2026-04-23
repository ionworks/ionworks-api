"""
Pipeline client for running parameterization workflows.

This module provides the :class:`PipelineClient` for creating and managing
pipelines that combine data fitting, calculations, and validation steps
for battery model parameterization.
"""

import os
import re
import time
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import IonworksError
from .validators import run_validators_outbound


def _prepare_payload(data: Any) -> Any:
    """Prepare payload for API submission using outbound validators pipeline."""
    return run_validators_outbound(data)


class DataFitConfig(BaseModel):
    """Configuration for data fitting step in a pipeline."""

    objectives: dict[str, Any]
    parameters: dict[str, Any]
    cost: dict[str, Any] | None = None
    optimizer: dict[str, Any] | None = None
    existing_parameters: dict[str, Any] | None = None


class EntryConfig(BaseModel):
    """Configuration for entry point in a pipeline."""

    values: dict[str, Any]


class BuiltInEntryConfig(BaseModel):
    """Configuration for built-in entry point in a pipeline."""

    name: str


class CalculationConfig(BaseModel):
    """Configuration for calculation step in a pipeline."""

    calculation: str
    electrode: str | None = None
    method: str | None = None
    existing_parameters: dict[str, Any] | None = None


class ValidationConfig(BaseModel):
    """Configuration for validation step in a pipeline."""

    objectives: dict[str, Any]
    summary_stats: list[Any]
    existing_parameters: dict[str, Any] | None = None


class PipelineConfig(BaseModel):
    """Configuration for a complete pipeline workflow."""

    project_id: str | None = Field(
        default=None,
        description="The project id to submit the pipeline to. "
        "Can be found in the project settings page. "
        "If not provided, will use PROJECT_ID environment variable.",
    )
    elements: dict[str, Any] = Field(
        description="Dictionary of elements defining the pipeline. The key is the name "
        "of the element and the value is the configuration of the element. "
    )
    name: str | None = Field(default=None, description="The name of the pipeline.")
    description: str | None = Field(
        default=None, description="The description of the pipeline."
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Dictionary of options for the pipeline. "
        "Options are used to configure the pipeline behavior. "
        "Available options are: "
        "live_progress_updates: bool ",
    )

    @field_validator("elements", mode="before")
    @classmethod
    def validate_elements_format(cls, v: Any) -> dict[str, Any]:
        """Validate that elements is a dict, not the old list format."""
        if isinstance(v, list):
            raise ValueError(
                "Pipeline elements must be provided as a dictionary, not a list. "
                "The format has changed from:\n"
                '  "elements": [{"name": {...}}, ...]\n'
                "to:\n"
                '  "elements": {"name": {...}, ...}\n'
                "Please update your pipeline configuration."
            )
        if not isinstance(v, dict):
            raise ValueError(
                f"Pipeline elements must be a dictionary, got {type(v).__name__}"
            )
        return v

    @model_validator(mode="after")
    def set_defaults(self) -> "PipelineConfig":
        """Set project_id from environment variable if not provided and defaults."""
        if self.project_id is None:
            env_project_id = os.getenv("PROJECT_ID")
            if env_project_id is None:
                raise ValueError(
                    "project_id is required. Either provide it in the config "
                    "or set the PROJECT_ID environment variable."
                )
            self.project_id = env_project_id
        # Ensure options is never None to avoid 422 errors
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

    def create(self, config: dict[str, Any]) -> PipelineSubmissionResponse:
        """Run a complete pipeline with the given configuration.

        Parameters
        ----------
        config : dict[str, Any]
            Dictionary containing configuration for the pipeline.

        Returns
        -------
        PipelineSubmissionResponse
            The pipeline submission response.

        Raises
        ------
        ValueError
            If the configuration is invalid.
        """
        try:
            validated_config = PipelineConfig(**config)
            payload = _prepare_payload(validated_config.model_dump())
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
                project_id = validated_config.project_id
                raise ValueError(
                    f"Access denied: The project '{project_id}' is not accessible "
                    "with your API key. Please verify that your API key has access "
                    "to this project."
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
            The project id to filter pipelines. If not provided, uses
            PROJECT_ID environment variable.
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
        if project_id is None:
            project_id = os.getenv("PROJECT_ID")
            if project_id is None:
                raise ValueError(
                    "project_id is required. Either provide it as an argument "
                    "or set the PROJECT_ID environment variable."
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
