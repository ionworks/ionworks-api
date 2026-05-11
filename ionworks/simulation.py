"""
Simulation client for running battery simulations.

This module provides the :class:`SimulationClient` for running battery
simulations using the Universal Cycler Protocol (UCP) format. It supports
single simulations, batch simulations with design of experiments (DOE),
and PyBaMM-based modeling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import time
from typing import Any, cast

from pydantic import BaseModel, Field, ValidationError

from .errors import IonworksError
from .job import JobResponse


class QuickModelConfig(BaseModel):
    """Quick model configuration for protocol-based simulations."""

    capacity: float = Field(default=1.0, description="Cell capacity in Ah")
    chemistry: str = Field(default="NMC/Graphite", description="Chemistry name")


class ProtocolExperimentConfig(BaseModel):
    """Protocol experiment configuration."""

    protocol: str = Field(description="YAML protocol string (UCP format)")
    name: str = Field(description="Protocol name for template naming")


class ProtocolSimulationRequest(BaseModel):
    """Request model for single protocol-based simulation."""

    parameterized_model: Any = Field(
        description=(
            "Model can be: quick_model dict, full model dict, or model ID string"
        )
    )
    protocol_experiment: ProtocolExperimentConfig = Field(
        description="Protocol experiment configuration"
    )
    experiment_parameters: dict[str, float] | None = Field(
        default=None,
        description=("Experiment parameters for any inputs in the protocol."),
    )
    design_parameters: dict[str, float] | None = Field(
        default=None, description="Design parameters for the simulation"
    )
    max_backward_jumps: int | None = Field(
        default=None,
        description="Maximum backward jumps allowed (for goto statements)",
    )
    study_id: str | None = Field(default=None, description="Optional study UUID")
    extra_variables: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of extra variables to include in simulation output "
            "(e.g., ['Negative electrode potential [V]', 'Positive electrode "
            "potential [V]']). If provided, these override any extra variables "
            "defined in the experiment template."
        ),
    )


class DOERow(BaseModel):
    """Design of experiments row configuration."""

    type: str = Field(description="Type: 'range', 'discrete', or 'normal'")
    name: str = Field(description="Parameter name")
    # For range type
    min: float | None = Field(default=None, description="Minimum value")
    max: float | None = Field(default=None, description="Maximum value")
    count: int | None = Field(
        default=None, description="Number of samples (for grid/random)"
    )
    # For discrete type
    values: list[float] | None = Field(default=None, description="Discrete values")
    # For normal type
    mean: float | None = Field(default=None, description="Mean value")
    std: float | None = Field(default=None, description="Standard deviation")


class DesignParametersDOE(BaseModel):
    """Design of experiments configuration."""

    sampling: str = Field(
        description="Sampling strategy: 'grid', 'random', or 'latin_hypercube'"
    )
    rows: list[DOERow] = Field(description="DOE row configurations")
    count: int | None = Field(
        default=None, description="Total count for non-grid sampling"
    )


class ProtocolSimulationBatchRequest(BaseModel):
    """Request model for batch protocol-based simulation."""

    parameterized_model: Any = Field(
        description=(
            "Model can be: quick_model dict, full model dict, or model ID string"
        )
    )
    protocol_experiment: ProtocolExperimentConfig = Field(
        description="Protocol experiment configuration"
    )
    design_parameters_doe: DesignParametersDOE = Field(
        description="Design of experiments configuration"
    )
    experiment_parameters: dict[str, float] | None = Field(
        default=None,
        description=("Experiment parameters for any inputs in the protocol."),
    )
    max_backward_jumps: int | None = Field(
        default=None,
        description="Maximum backward jumps allowed (for goto statements)",
    )
    study_id: str | None = Field(default=None, description="Optional study UUID")
    extra_variables: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of extra variables to include in simulation output "
            "(e.g., ['Negative electrode potential [V]', 'Positive electrode "
            "potential [V]']). If provided, these override any extra variables "
            "defined in the experiment template."
        ),
    )


class SimulationResponse(BaseModel):
    """Response model for simulation creation."""

    simulation_id: str = Field(description="Simulation UUID")
    job_id: str = Field(description="Job UUID")


class SimulationClient:
    """Client for running simulations."""

    def __init__(self, client: Any) -> None:
        """Initialize the SimulationClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def protocol(self, config: dict[str, Any]) -> SimulationResponse:
        """Create a single protocol-based simulation.

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary containing:

            - parameterized_model: quick_model dict, full model dict,
              or model ID string
            - protocol_experiment: ProtocolExperimentConfig dict with
              protocol and name fields
            - experiment_parameters: Optional dict with initial_soc and
              initial_temperature
            - design_parameters: Optional dict[str, float]
            - max_backward_jumps: Optional int
            - study_id: Optional str
            - extra_variables: Optional list[str] — extra variables to
              include in simulation output

        Returns
        -------
        SimulationResponse
            Response containing simulation_id and job_id.

        Raises
        ------
        ValueError
            If the configuration is invalid.
        """
        endpoint = "/simulations/protocol"
        try:
            validated_config = ProtocolSimulationRequest(**config)
            response_data = self.client.post(
                endpoint, json_payload=validated_config.model_dump(exclude_none=True)
            )
            return SimulationResponse(**response_data)
        except ValidationError as e:
            raise ValueError(f"Invalid protocol simulation configuration: {e}") from e

    def protocol_batch(self, config: dict[str, Any]) -> list[SimulationResponse]:
        """Create multiple protocol-based simulations using DOE.

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary containing:

            - parameterized_model: quick_model dict, full model dict,
              or model ID string
            - protocol_experiment: ProtocolExperimentConfig dict with
              protocol and name fields
            - design_parameters_doe: DesignParametersDOE dict
            - experiment_parameters: Optional dict
            - max_backward_jumps: Optional int
            - study_id: Optional str
            - extra_variables: Optional list[str] — extra variables to
              include in simulation output

        Returns
        -------
        list[SimulationResponse]
            List of responses, each containing simulation_id and job_id.

        Raises
        ------
        ValueError
            If the configuration is invalid.
        """
        endpoint = "/simulations/protocol/batch"
        try:
            validated_config = ProtocolSimulationBatchRequest(**config)
            response_data = self.client.post(
                endpoint, json_payload=validated_config.model_dump(exclude_none=True)
            )
            if not isinstance(response_data, list):
                msg = (
                    f"Unexpected response format from {endpoint}: expected a "
                    f"list, got {type(response_data).__name__}"
                )
                raise ValueError(msg)
            return [SimulationResponse(**item) for item in response_data]
        except ValidationError as e:
            raise ValueError(
                f"Invalid batch protocol simulation configuration: {e}"
            ) from e

    def list(
        self,
        parameterized_model_id: str | None = None,
        study_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List simulations filtered by parameterized model or study.

        Exactly one of ``parameterized_model_id`` or ``study_id`` must be provided.

        Parameters
        ----------
        parameterized_model_id : str, optional
            Filter simulations belonging to this parameterized model.
        study_id : str, optional
            Filter simulations assigned to this study.

        Returns
        -------
        list[dict[str, Any]]
            List of simulation summary objects.
        """
        if bool(parameterized_model_id) == bool(study_id):
            raise ValueError(
                "Exactly one of 'parameterized_model_id' or 'study_id' must be provided."
            )
        params: dict[str, str] = {}
        if parameterized_model_id:
            params["parameterized_model_id"] = parameterized_model_id
        else:
            params["study_id"] = study_id  # type: ignore[assignment]
        query = "&".join(f"{k}={v}" for k, v in params.items())
        endpoint = f"/simulations?{query}"
        response_data = self.client.get(endpoint)
        # Handle StudySimulationsResponse envelope from the backend
        if isinstance(response_data, dict) and "simulations" in response_data:
            return response_data["simulations"]
        if not isinstance(response_data, list):
            msg = (
                f"Unexpected response format from {endpoint}: expected a list, "
                f"got {type(response_data).__name__}"
            )
            raise ValueError(msg)
        return response_data

    def get(self, simulation_id: str) -> dict[str, Any]:
        """Get a specific simulation by ID.

        Parameters
        ----------
        simulation_id : str
            The UUID of the simulation to retrieve.

        Returns
        -------
        dict[str, Any]
            Simulation object with full joined data including model, experiment,
            and simulation_data (null if not completed).
        """
        endpoint = f"/simulations/{simulation_id}"
        response_data = self.client.get(endpoint)
        return cast(dict[str, Any], response_data)

    def get_result(self, simulation_id: str) -> dict[str, Any]:
        """Get simulation data/result for a completed simulation.

        Parameters
        ----------
        simulation_id : str
            The UUID of the simulation.

        Returns
        -------
        dict[str, Any]
            Simulation data object containing time_series, steps, and metrics.
            Returns 404 if simulation hasn't completed yet.

        Raises
        ------
        Exception
            If simulation data not found (simulation may not be completed yet).
            The client will raise an appropriate error for 404 responses.
        """
        endpoint = f"/simulations/{simulation_id}/result"
        response_data = self.client.get(endpoint)
        return cast(dict[str, Any], response_data)

    def _poll_simulations(
        self,
        simulation_ids: list[str],
        timeout: int,
        poll_interval: int,
        verbose: bool,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, JobResponse]]:
        """Poll simulations until all complete/fail or timeout is reached.

        Parameters
        ----------
        simulation_ids : list[str]
            List of simulation IDs to poll.
        timeout : int
            Maximum time to wait in seconds.
        poll_interval : int
            Time between polls in seconds.
        verbose : bool
            Whether to print status updates.

        Returns
        -------
        tuple[dict[str, dict[str, Any]], dict[str, JobResponse]]
            A two-element tuple of ``(completed, failed)`` mappings — completed
            holds the simulation payload, failed holds the typed JobResponse
            for each terminally-failed simulation.

        Raises
        ------
        TimeoutError
            If no simulations reach a terminal state within the timeout.
        IonworksError
            Propagated from the underlying simulation or job-status requests.
        """
        timeout_delta = timedelta(seconds=timeout)
        start_time = datetime.now(UTC)
        completed: dict[str, dict[str, Any]] = {}
        failed: dict[str, JobResponse] = {}

        if verbose:
            print(f"Polling for {len(simulation_ids)} simulation(s) completion...")

        while datetime.now(UTC) - start_time < timeout_delta:
            for sim_id in simulation_ids:
                if sim_id in completed or sim_id in failed:
                    continue
                simulation = self.get(sim_id)
                if (
                    simulation.get("storage_folder")
                    or simulation.get("simulation_data")  # Legacy fallback
                ):
                    completed[sim_id] = simulation
                    continue

                job_id = simulation.get("job_id")
                if job_id:
                    job = self.client.job.get(job_id)
                    if job.is_failed:
                        failed[sim_id] = job
                        if verbose:
                            error = job.error or "unknown error"
                            print(f"  Simulation {sim_id} {job.status}: {error}")

            elapsed = int((datetime.now(UTC) - start_time).total_seconds())
            terminal = len(completed) + len(failed)
            if verbose:
                parts = [f"{len(completed)} completed"]
                if failed:
                    parts.append(f"{len(failed)} failed")
                print(
                    f"  Status: {', '.join(parts)} "
                    f"of {len(simulation_ids)} (elapsed: {elapsed}s)"
                )

            if terminal == len(simulation_ids):
                if verbose and not failed:
                    print("All simulations completed!")
                return completed, failed

            time.sleep(poll_interval)

        # Timeout reached
        if verbose:
            print(
                f"Timeout: {len(completed)} completed, {len(failed)} failed "
                f"of {len(simulation_ids)} within {timeout} seconds"
            )
        if not completed and not failed:
            msg = f"No simulations completed within {timeout} seconds"
            raise TimeoutError(msg)
        return completed, failed

    def wait_for_completion(
        self,
        simulation_id: str | list[str],
        timeout: int = 60,
        poll_interval: int = 2,
        verbose: bool = True,
        raise_on_failure: bool = True,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Wait for simulation(s) to complete by polling until done or timeout.

        Parameters
        ----------
        simulation_id : str | list[str]
            Single simulation ID or list of simulation IDs to wait for.
            Can also be a :class:`SimulationResponse` or list of them (the
            ``job_id`` will be extracted automatically for failure detection).
        timeout : int
            Maximum time to wait in seconds (default: 60).
        poll_interval : int
            Time between polls in seconds (default: 2).
        verbose : bool
            Whether to print status updates (default: True).
        raise_on_failure : bool
            Whether to raise :class:`IonworksError` when a simulation's
            job fails or is canceled (default: True).

        Returns
        -------
        dict[str, Any] | list[dict[str, Any]]
            Completed simulation(s). Returns single dict if single ID
            provided, list of dicts if list of IDs provided. Only returns
            completed simulations if timeout is reached.

        Raises
        ------
        TimeoutError
            If timeout is reached before all simulations complete.
        IonworksError
            If a simulation fails and *raise_on_failure* is True.
        """
        is_single = isinstance(simulation_id, str)
        simulation_ids = [simulation_id] if is_single else simulation_id  # type: ignore[list-item]

        completed, failed = self._poll_simulations(
            simulation_ids, timeout, poll_interval, verbose
        )

        if is_single:
            sid = simulation_ids[0]
            if sid in failed and raise_on_failure:
                job = failed[sid]
                error = job.error or "unknown error"
                raise IonworksError(f"Simulation {sid} {job.status}: {error}")
            if sid not in completed:
                if sid in failed:
                    return failed[sid].model_dump()
                msg = f"Simulation {sid} did not complete within {timeout} seconds"
                raise TimeoutError(msg)
            return completed[sid]

        # Batch mode
        if failed:
            if raise_on_failure:
                failure_msgs = []
                for sid, job in failed.items():
                    error = job.error or "unknown error"
                    failure_msgs.append(f"  {sid}: {job.status}: {error}")
                msg = f"{len(failed)} simulation(s) failed:\n" + "\n".join(failure_msgs)
                raise IonworksError(msg)
            import warnings

            warnings.warn(
                f"{len(failed)} simulation(s) failed and were excluded from "
                f"results: {list(failed)}",
                stacklevel=2,
            )
        return [completed[sim_id] for sim_id in simulation_ids if sim_id in completed]
