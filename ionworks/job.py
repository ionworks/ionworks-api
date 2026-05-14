"""Job client for managing asynchronous jobs.

This module provides the :class:`JobClient` for submitting, monitoring, and
managing background jobs in the Ionworks platform.
"""

from typing import Any

from pydantic import BaseModel, ValidationError


class JobCreationPayload(BaseModel):
    """Payload for creating a job."""

    job_type: str
    params: dict[str, Any]
    priority: int = 5  # Default priority
    callback_url: str | None = None  # Optional callback


class JobResponse(BaseModel):
    """Response model for job details."""

    job_id: str
    status: str
    job_type: str
    priority: int
    created_at: str
    updated_at: str
    error: str | None = None
    result: dict[str, Any] | None = None
    #: Server-derived flag: the job has reached a terminal state and will not
    #: transition further. Read from the wire so the client never needs to
    #: hard-code which status strings count as terminal.
    is_terminal: bool
    #: Server-derived flag: the job terminated unsuccessfully (failed or
    #: canceled). ``is_terminal and not is_failed`` means successful completion.
    is_failed: bool


class JobClient:
    """Client for managing asynchronous jobs.

    This class provides methods to create, retrieve, list, and cancel jobs
    in the Ionworks platform.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the JobClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API requests.
        """
        self.client = client

    def create(self, payload: JobCreationPayload) -> JobResponse:
        """Submit a job using the provided payload.

        Parameters
        ----------
        payload : JobCreationPayload
            The configuration for the job to be created.

        Returns
        -------
        JobResponse
            Response containing the job_id and initial status.

        Raises
        ------
        requests.exceptions.RequestException
            If the API request fails.
        ValueError
            If the response parsing fails.
        """
        endpoint = "/jobs/"
        try:
            response_data = self.client.post(
                endpoint, json_payload=payload.model_dump(exclude_none=True)
            )
            # Pydantic validation is applied here
            return JobResponse(**response_data)
        except ValidationError as e:
            # Catch Pydantic validation errors specifically
            msg = f"Invalid response format received from {endpoint}: {e}"
            raise ValueError(msg) from e
        # RequestExceptions (including HTTPError) are handled by client._post

    def get(self, job_id: str) -> JobResponse:
        """Get the status and details of a specific job.

        Parameters
        ----------
        job_id : str
            The ID of the job to retrieve.

        Returns
        -------
        JobResponse
            Job details and current status.

        Raises
        ------
        ValueError
            If the response parsing fails.
        """
        endpoint = f"/jobs/{job_id}"
        try:
            response_data = self.client.get(endpoint)
            return JobResponse(**response_data)
        except ValidationError as e:
            msg = f"Invalid response format received from {endpoint}: {e}"
            raise ValueError(msg) from e

    def get_metadata(self, job_id: str) -> dict[str, Any]:
        """Get the full metadata blob for a job.

        Returns the parsed JSON contents of the job's metadata storage object,
        which holds large fields that are stripped from the ``result`` column
        — for example the ``validation_results`` and ``validation_plot_config``
        payloads written by pipeline validation jobs.

        Parameters
        ----------
        job_id : str
            The ID of the job whose metadata to retrieve.

        Returns
        -------
        dict[str, Any]
            The parsed metadata payload.
        """
        endpoint = f"/jobs/{job_id}/metadata"
        return self.client.get(endpoint)

    def list(self) -> list[JobResponse]:
        """List all jobs.

        Returns
        -------
        list[JobResponse]
            List of all jobs with their details.

        Raises
        ------
        ValueError
            If the response is not a list or job data format is invalid.
        """
        endpoint = "/jobs/"
        response_data = self.client.get(endpoint)
        # Ensure response_data is a list before list comprehension
        if not isinstance(response_data, list):
            msg = (
                f"Unexpected response format from {endpoint}: expected a list, "
                f"got {type(response_data).__name__}"
            )
            raise ValueError(msg)
        # Apply validation within list comprehension
        try:
            return [JobResponse(**job) for job in response_data]
        except ValidationError as e:
            msg = f"Invalid job data format received from {endpoint}: {e}"
            raise ValueError(msg) from e

    def cancel(self, job_id: str) -> JobResponse:
        """Cancel a job.

        Parameters
        ----------
        job_id : str
            The ID of the job to cancel.

        Returns
        -------
        JobResponse
            Updated job details with canceled status.

        Raises
        ------
        ValueError
            If the response parsing fails.
        """
        endpoint = f"/jobs/{job_id}/cancel"
        try:
            response_data = self.client.post(endpoint, json_payload={})
            return JobResponse(**response_data)
        except ValidationError as e:
            msg = f"Invalid response format received from {endpoint}: {e}"
            raise ValueError(msg) from e
