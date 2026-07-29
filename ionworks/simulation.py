"""
Simulation client for running battery simulations.

This module provides the :class:`SimulationClient` for running battery
simulations using the Universal Cycler Protocol (UCP) format. It supports
single simulations, batch simulations with design of experiments (DOE),
and PyBaMM-based modeling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import time
from typing import Any, cast

import polars as pl
from pydantic import BaseModel, Field, ValidationError

from .errors import IonworksError
from .job import JobResponse
from .validators import DataFrame, get_dataframe_backend


def _dict_of_lists_to_df(data: dict[str, list[Any]]) -> DataFrame:
    """Convert a dict-of-lists payload to a DataFrame using the active backend."""
    if get_dataframe_backend() == "pandas":
        import pandas as pd

        return pd.DataFrame(data)
    return pl.DataFrame(data)


@dataclass(eq=False)
class SimulationResult:
    """Typed result returned by :meth:`SimulationClient.get_result`."""

    #: Time-series data with one row per time point. Column names follow the
    #: platform convention (e.g. ``"Time [s]"``, ``"Voltage [V]"``). Returns
    #: a polars DataFrame by default; a pandas DataFrame when
    #: ``set_dataframe_backend("pandas")`` is active.
    time_series: DataFrame
    #: Step-level summary with one row per protocol step. Returns the same
    #: DataFrame type as ``time_series``.
    steps: DataFrame
    #: Scalar metrics computed over the full simulation (e.g. cycle-level
    #: summaries). Not tabular; returned as a plain dict.
    metrics: dict[str, Any]


class QuickModelConfig(BaseModel):
    """Quick model configuration for protocol-based simulations.

    A quick model builds a system ECM from a nominal capacity and a chemistry
    name — it does not take a base ``model_id`` (that is what a full
    parameterized model is for).
    """

    capacity: float = Field(default=1.0, description="Cell capacity in Ah")
    chemistry: str = Field(default="NMC/Graphite", description="Chemistry name")


#: Keys the backend accepts inside a quick-model config. Used to recognise a
#: flat quick-model dict passed as ``parameterized_model`` and re-nest it under
#: the ``"quick_model"`` key the batch endpoint expects.
_QUICK_MODEL_KEYS = ("capacity", "chemistry", "resistance_pct", "cell_spec_id")


def _normalize_parameterized_model(parameterized_model: Any) -> Any:
    """Normalise a ``parameterized_model`` value into a backend-accepted shape.

    The batch endpoint recognises a quick model only when its fields are nested
    under a ``"quick_model"`` key. Users (and the QuickModelConfig helper)
    naturally pass a *flat* ``{"capacity": ..., "chemistry": ...}`` dict, which
    would otherwise fall through to full-model resolution and fail. This wraps
    such a flat dict so quick models work as documented. A full model dict
    (has ``model_id``/``parameters``), an already-nested ``{"quick_model": ...}``
    dict, or a model-id string passes through unchanged.

    Parameters
    ----------
    parameterized_model : Any
        A quick-model dict, ``QuickModelConfig``, full model dict, or model-id
        string.

    Returns
    -------
    Any
        The same value, with a flat quick-model dict re-nested under
        ``"quick_model"``.
    """
    if isinstance(parameterized_model, QuickModelConfig):
        return {"quick_model": parameterized_model.model_dump()}
    if isinstance(parameterized_model, dict):
        pm = parameterized_model
        is_flat_quick_model = (
            "quick_model" not in pm
            and "model_id" not in pm
            and "parameters" not in pm
            and any(k in pm for k in _QUICK_MODEL_KEYS)
        )
        if is_flat_quick_model:
            return {"quick_model": {k: pm[k] for k in _QUICK_MODEL_KEYS if k in pm}}
    return parameterized_model


class ProtocolExperimentConfig(BaseModel):
    """Protocol experiment configuration."""

    protocol: str = Field(description="YAML protocol string (UCP format)")
    name: str = Field(description="Protocol name for template naming")


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
            "Model can be: a quick-model dict {'capacity': <Ah>, 'chemistry': "
            "<name>} (builds a system ECM; no base model_id), a QuickModelConfig, "
            "a full model dict {'model_id': ..., 'parameters': {...}}, or a "
            "parameterized-model ID string."
        )
    )
    protocol_experiment: ProtocolExperimentConfig = Field(
        description="Protocol experiment configuration"
    )
    design_parameters_doe: DesignParametersDOE | None = Field(
        default=None,
        description="Design of experiments configuration. Omit for a single simulation.",
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


def _design_parameters_to_single_row_doe(
    design_parameters: dict[str, float],
) -> DesignParametersDOE:
    """Translate a flat ``design_parameters`` mapping to a one-row discrete DOE.

    Each entry becomes a ``discrete`` :class:`DOERow` with a single-element
    ``values`` list, wrapped in a ``grid`` :class:`DesignParametersDOE` — the
    canonical single-simulation shape consumed by ``protocol_batch``.
    """
    return DesignParametersDOE(
        sampling="grid",
        rows=[
            DOERow(type="discrete", name=name, values=[float(value)])
            for name, value in design_parameters.items()
        ],
    )


def _expected_doe_simulation_count(doe: DesignParametersDOE) -> int:
    """Return the number of simulations a DOE will produce.

    For ``grid`` sampling, this is the product of the per-row sample counts
    (``len(values)`` for discrete rows, ``count`` for range rows). For
    ``random`` and ``latin_hypercube`` sampling, it is the top-level ``count``
    (defaulting to 1 when unset). Used by :meth:`SimulationClient.protocol`
    to refuse multi-simulation DOEs before billing them.
    """
    if doe.sampling == "grid":
        total = 1
        for row in doe.rows:
            if row.values is not None:
                total *= len(row.values)
            elif row.count is not None:
                total *= row.count
            else:
                raise ValueError(
                    f"DOE row {row.name!r} is missing 'values' (discrete) or "
                    "'count' (range) — cannot compute simulation count."
                )
        return total
    return doe.count or 1


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

        Delegates to :meth:`protocol_batch` and returns the single result. Pass
        the same config fields as :meth:`protocol_batch`, plus the
        single-simulation convenience field ``design_parameters`` (a flat
        ``dict[str, float]``) — it is translated internally to a one-row
        discrete DOE.

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary containing:

            - parameterized_model: one of
                - a quick-model dict ``{"capacity": <Ah>, "chemistry": <name>}``
                  (builds a system ECM; no base ``model_id``),
                - a ``QuickModelConfig``,
                - a full model dict ``{"model_id": ..., "parameters": {...}}``, or
                - a parameterized-model ID string.
            - protocol_experiment: ProtocolExperimentConfig dict with
              protocol and name fields
            - experiment_parameters: Optional dict with initial_soc and
              initial_temperature
            - design_parameters: Optional ``dict[str, float]`` — design
              parameter overrides for this single simulation (translated to
              a single-row discrete DOE under the hood).
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
            If the configuration is invalid, both ``design_parameters`` and
            ``design_parameters_doe`` are supplied, or a multi-row
            ``design_parameters_doe`` is supplied (use :meth:`protocol_batch`
            for multi-simulation DOE).
        """
        config = dict(config)
        design_parameters = config.pop("design_parameters", None)
        existing_doe = config.get("design_parameters_doe")
        if design_parameters is not None:
            if existing_doe is not None:
                raise ValueError(
                    "Pass either 'design_parameters' (single-sim convenience) "
                    "or 'design_parameters_doe' (DOE), not both."
                )
            doe = (
                _design_parameters_to_single_row_doe(design_parameters)
                if design_parameters
                else None
            )
        elif existing_doe is not None:
            doe = DesignParametersDOE.model_validate(existing_doe)
        else:
            doe = None

        if doe is not None:
            expected = _expected_doe_simulation_count(doe)
            if expected != 1:
                raise ValueError(
                    f"protocol() is single-simulation, but the supplied DOE "
                    f"would produce {expected} simulations. Use "
                    "protocol_batch() for multi-simulation DOE runs."
                )
            config["design_parameters_doe"] = doe.model_dump(exclude_none=True)

        results = self.protocol_batch(config)
        return results[0]

    def protocol_batch(self, config: dict[str, Any]) -> list[SimulationResponse]:
        """Create multiple protocol-based simulations using DOE.

        Uses a two-step flow: first parses the protocol and creates an experiment
        template (``POST /protocols/parse-to-template``), then runs the batch
        (``POST /simulations/with-template/batch``).

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary containing:

            - parameterized_model: one of
                - a quick-model dict ``{"capacity": <Ah>, "chemistry": <name>}``
                  (builds a system ECM; no base ``model_id``),
                - a ``QuickModelConfig``,
                - a full model dict ``{"model_id": ..., "parameters": {...}}``, or
                - a parameterized-model ID string.
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
        try:
            validated_config = ProtocolSimulationBatchRequest(**config)
        except ValidationError as e:
            raise ValueError(
                f"Invalid batch protocol simulation configuration: {e}"
            ) from e

        experiment_parameters = validated_config.experiment_parameters or {}

        # Step 1: parse protocol and resolve/create the experiment template
        parse_response = self.client.post(
            "/protocols/parse-to-template",
            json_payload={
                "protocol_experiment": validated_config.protocol_experiment.model_dump(),
                "experiment_parameters": experiment_parameters,
            },
        )
        template_id: str = parse_response["template_id"]

        # Step 2: run the batch against the resolved template
        batch_payload: dict[str, Any] = {
            "parameterized_model": _normalize_parameterized_model(
                validated_config.parameterized_model
            ),
            "experiment_template_id": template_id,
            "experiment_parameter_sets": [experiment_parameters],
            "design_parameters_doe": validated_config.design_parameters_doe.model_dump(
                exclude_none=True
            )
            if validated_config.design_parameters_doe
            else None,
            "max_backward_jumps": validated_config.max_backward_jumps,
            "study_id": validated_config.study_id,
            "extra_variables": validated_config.extra_variables,
        }
        # Strip None values so the backend uses its own defaults
        batch_payload = {k: v for k, v in batch_payload.items() if v is not None}

        batch_endpoint = "/simulations/with-template/batch"
        response_data = self.client.post(batch_endpoint, json_payload=batch_payload)
        if not isinstance(response_data, list):
            msg = (
                f"Unexpected response format from {batch_endpoint}: expected a "
                f"list, got {type(response_data).__name__}"
            )
            raise ValueError(msg)
        return [SimulationResponse(**item) for item in response_data]

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
        # The endpoint returns {simulations: [...], model_scalar_parameters: {...}}
        if isinstance(response_data, dict) and "simulations" in response_data:
            return cast(list[dict[str, Any]], response_data["simulations"])
        if isinstance(response_data, list):
            return response_data
        msg = (
            f"Unexpected response format from {endpoint}: expected a dict with "
            f"'simulations' key or a list, got {type(response_data).__name__}"
        )
        raise ValueError(msg)

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

    def get_result(self, simulation_id: str) -> SimulationResult:
        """Get simulation data/result for a completed simulation.

        Parameters
        ----------
        simulation_id : str
            The UUID of the simulation.

        Returns
        -------
        SimulationResult
            Typed result with ``time_series`` and ``steps`` as DataFrames and
            ``metrics`` as a plain dict. DataFrame type (polars or pandas) follows
            the active backend set via ``set_dataframe_backend()``.

        Raises
        ------
        IonworksError
            If the API request fails. A 404 typically means the result is not
            yet available (simulation still running or queued). Other status codes
            indicate authentication failures, server errors, or a missing
            simulation ID.
        """
        endpoint = f"/simulations/{simulation_id}/result"
        raw = cast(dict[str, Any], self.client.get(endpoint))
        return SimulationResult(
            time_series=_dict_of_lists_to_df(raw.get("time_series") or {}),
            steps=_dict_of_lists_to_df(raw.get("steps") or {}),
            metrics=raw.get("metrics") or {},
        )

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
