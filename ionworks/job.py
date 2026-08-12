"""Job client for managing asynchronous jobs.

This module provides the :class:`JobClient` for submitting, monitoring, and
managing background jobs in the Ionworks platform.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ionworks.errors import IonworksError
from ionworks.models import _build_endpoint


class JobCreationPayload(BaseModel):
    """Payload for creating a job."""

    job_type: str
    params: dict[str, Any]
    priority: int = 5  # Default priority
    callback_url: str | None = None  # Optional callback


class JobResponse(BaseModel):
    """Response model for job details.

    Extra fields are retained so additive backend response fields remain
    available without requiring a lock-step SDK release.
    """

    model_config = ConfigDict(extra="allow")

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
        which holds large fields that are stripped from the ``result`` column.
        Notable keys include the ``validation_results`` and
        ``validation_plot_config`` payloads written by pipeline validation jobs,
        and ``intermediate_results`` — the per-iteration parameter trace logged
        during datafit and optimization runs (see :meth:`get_parameter_trace`).

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

    def get_plot_data(
        self,
        job_id: str,
        objective_name: str,
        plot_index: int = 0,
        max_points: int = 2000,
        x_min: float | None = None,
        x_max: float | None = None,
    ) -> dict[str, Any]:
        """Get decimated model-vs-data plot traces for a data-fit job.

        Wraps ``GET /pipelines/datafits/{job_id}/plot_data``. A data-fit job re-runs a
        validation on its best-fit parameters and stores the overlay in its own
        metadata, so this returns the fit's model-vs-data traces directly — no
        separate validation element or raw-metadata polling required. Traces are
        downsampled to at most ``max_points`` points; refetch with ``x_min`` /
        ``x_max`` set to the current viewport for semantic zoom.

        Parameters
        ----------
        job_id : str
            The data-fit job whose plot data to fetch.
        objective_name : str
            Objective name within the job's ``validation_plot_config`` (the key
            used for the objective in the DataFit's ``objectives`` mapping).
        plot_index : int, optional
            Index into the objective's list of plots. Defaults to 0.
        max_points : int, optional
            Maximum points returned per trace (100-10000). Defaults to 2000.
        x_min : float, optional
            Lower x-range bound (inclusive). Omit for the full range.
        x_max : float, optional
            Upper x-range bound (inclusive). Omit for the full range.

        Returns
        -------
        dict[str, Any]
            The decimated plot-data payload for the requested plot.
        """
        endpoint = _build_endpoint(
            f"/pipelines/datafits/{job_id}/plot_data",
            {
                "objective_name": objective_name,
                "plot_index": plot_index,
                "max_points": max_points,
                "x_min": x_min,
                "x_max": x_max,
            },
        )
        return self.client.get(endpoint)

    def get_parameter_trace(self, job_id: str) -> list[dict[str, Any]]:
        """Get the per-iteration parameter trace for a datafit or optimization job.

        Datafit and optimization runs log the optimizer's progress to the job
        metadata under ``intermediate_results`` — one entry per saved iteration.
        This is the same data the web UI plots as parameter and cost-convergence
        traces. Each entry contains:

        - ``best_cost`` : float
            Best (lowest) objective value seen up to this iteration.
        - ``cost`` : float
            Objective value at this iteration.
        - ``inputs`` : dict
            Scaled parameter values at this iteration.
        - ``inputs_unscaled`` : dict
            Unscaled (physical) parameter values at this iteration — keyed by
            parameter name. Use these for parameter traces.
        - ``multistart_job_id`` : int
            Index of the multistart this entry belongs to, when applicable.
        - ``outputs`` / ``best_outputs`` : dict, optional
            Model outputs at this iteration, present for design-objective runs.

        Saves are throttled (roughly every 100 iterations or every 5 seconds),
        so the trace is a sampled subset of the optimizer's evaluations rather
        than every single one, and is empty when live progress updates were
        disabled for the run. There is no per-iteration wall-clock timing.

        Parameters
        ----------
        job_id : str
            The ID of the datafit or optimization job whose trace to retrieve.

        Returns
        -------
        list[dict[str, Any]]
            The per-iteration trace entries, ordered oldest first. Empty if the
            job recorded no intermediate results.
        """
        metadata = self.get_metadata(job_id)
        trace = metadata.get("intermediate_results")
        return trace if isinstance(trace, list) else []

    def get_posterior_samples(self, job_id: str) -> dict[str, Any]:
        """Get the sample chain for a sampler-based datafit job.

        A datafit run with a sampler (e.g. ``PintsSampler``) evaluates many
        parameter vectors rather than converging on a single point estimate.
        The full chain is too large for the job ``result`` column, so it is
        offloaded to the job metadata blob, returned here as:

        - ``samples`` : dict[str, list]
            The chain, keyed by parameter name. Each value is nested
            ``(starts, iterations)`` for a multistart fit and a flat
            per-iteration list for a single start, so index the iteration axis
            last (``[..., burnin:]``) to handle both.
        - ``sample_costs`` : list
            The cost/objective value for each sample, shaped like one
            parameter's chain.
        - ``sample_param_names`` : list[str]
            The parameter names in column order.
        - ``sample_burnin`` : int or None
            Number of initial chain iterations the sampler treats as burn-in.
            The chain includes them; discard them before analysis. ``None`` for
            samplers with no burn-in concept (``GridSearch``,
            ``PointEstimateSampler``).

        Every sampler populates these keys, not only Bayesian ones. Fits driven
        by a conventional optimizer (``CMAES``, ``ScipyMinimize``), along with
        optimization and validation jobs, return an empty dict. So does any job
        with no metadata blob to read — one that failed before writing one, an
        unknown ``job_id``, or a job in another organization all surface as a
        404, treated here as "no samples" rather than an error.

        Parameters
        ----------
        job_id : str
            The ID of the datafit job whose sample chain to retrieve.

        Returns
        -------
        dict[str, Any]
            A dict with ``samples``, ``sample_costs``, ``sample_param_names``,
            and ``sample_burnin`` keys. Empty if the job recorded no samples.
        """
        try:
            metadata = self.get_metadata(job_id)
        except IonworksError as exc:
            if exc.status_code == 404:
                return {}
            raise
        samples = metadata.get("samples")
        if not isinstance(samples, dict):
            return {}
        return {
            "samples": samples,
            "sample_costs": metadata.get("sample_costs"),
            "sample_param_names": metadata.get("sample_param_names"),
            "sample_burnin": metadata.get("sample_burnin"),
        }

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
