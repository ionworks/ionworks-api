"""
Simulation client for running battery simulations.

This module provides the :class:`SimulationClient` for running battery
simulations using the Universal Cycler Protocol (UCP) format. It supports
single simulations, batch simulations with design of experiments (DOE),
and PyBaMM-based modeling.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import time
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ._dataframe import parquet_to_dataframe
from ._project_id import resolve_project_id
from ._simulation_columns import (
    ensure_steps_delta_columns,
    ensure_time_series_capacity_columns,
)
from .errors import IonworksError
from .job import JobResponse
from .models import PaginatedList, _build_endpoint, _parse_list_response
from .validators import DataFrame


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


class _StrictModel(BaseModel):
    """Base for request configs: a misspelt or misplaced key raises.

    Otherwise it would be dropped silently, running a simulation without the
    setting the caller asked for.
    """

    model_config = ConfigDict(extra="forbid")


class ProtocolExperimentConfig(_StrictModel):
    """Protocol experiment configuration."""

    protocol: str = Field(description="YAML protocol string (UCP format)")
    name: str = Field(description="Protocol name for template naming")


class DOERow(_StrictModel):
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


class DesignParametersDOE(_StrictModel):
    """Design of experiments configuration."""

    sampling: str = Field(
        description="Sampling strategy: 'grid', 'random', or 'latin_hypercube'"
    )
    rows: list[DOERow] = Field(description="DOE row configurations")
    count: int | None = Field(
        default=None, description="Total count for non-grid sampling"
    )


class SimulationCondition(_StrictModel):
    """The inputs of one simulation in an explicit list of runs."""

    experiment_parameters: dict[str, float | str] = Field(
        default_factory=dict,
        description=(
            "Values for the protocol's inputs in this run, e.g. "
            "{'Temperature [°C]': 25}. Override the request's shared "
            "experiment_parameters."
        ),
    )
    design_parameters: dict[str, float | str] = Field(
        default_factory=dict,
        description="Model-parameter overrides for this run.",
    )


class ProtocolSimulationBatchRequest(_StrictModel):
    """Request model for batch protocol-based simulation."""

    parameterized_model: Any = Field(
        description=(
            "Model can be: a quick-model dict {'capacity': <Ah>, 'chemistry': "
            "<name>} (builds a system ECM; no base model_id), a QuickModelConfig, "
            "a full model dict {'model_id': ..., 'parameters': {...}}, or a "
            "parameterized-model ID string."
        )
    )
    protocol_experiment: ProtocolExperimentConfig | None = Field(
        default=None,
        description=(
            "Protocol experiment configuration (UCP YAML plus a name). Provide "
            "exactly one of this or ``protocol_id``."
        ),
    )
    protocol_id: str | None = Field(
        default=None,
        description=(
            "Id of a saved protocol to run. Provide exactly one of this or "
            "``protocol_experiment``."
        ),
    )
    design_parameters_doe: DesignParametersDOE | None = Field(
        default=None,
        description="Design of experiments configuration. Omit for a single simulation.",
    )
    conditions: list[SimulationCondition] | None = Field(
        default=None,
        description=(
            "Explicit list of runs, one simulation per entry, each with its own "
            "experiment and design parameters. Use in place of "
            "``design_parameters_doe`` when the runs are not a sampled design."
        ),
    )
    experiment_parameters: dict[str, float | str] | None = Field(
        default=None,
        description=(
            "Experiment parameters for any inputs in the protocol. With "
            "``conditions``, a shared default each condition can override."
        ),
    )
    max_backward_jumps: int | None = Field(
        default=None,
        description=(
            "Maximum backward jumps allowed (for goto statements). Defaults "
            "to 1000 when unset."
        ),
    )
    study_id: str | None = Field(default=None, description="Optional study UUID")
    project_id: str | None = Field(
        default=None,
        description=(
            "Project the protocol belongs to. Required by the backend for "
            "``protocol_experiment``: the parsed protocol is created as a "
            "project-scoped experiment template. Falls back to the project_id "
            "configured on the Ionworks client (or IONWORKS_PROJECT_ID) when "
            "omitted. With ``protocol_id`` it is optional, and the saved "
            "protocol's own project is used when it is omitted."
        ),
    )
    extra_variables: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of extra model variables to include in simulation "
            "output (e.g., ['Anode potential [V]', 'Cathode potential [V]']). "
            "A name the model does not carry is refused when the simulation is "
            "submitted. If provided, these override any extra variables "
            "defined in the experiment template."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_protocol_source(self) -> ProtocolSimulationBatchRequest:
        if (self.protocol_experiment is None) == (self.protocol_id is None):
            raise ValueError(
                "Provide exactly one of 'protocol_experiment' (UCP YAML) or "
                "'protocol_id' (a saved protocol)."
            )
        return self

    @model_validator(mode="after")
    def _at_most_one_run_list(self) -> ProtocolSimulationBatchRequest:
        if self.conditions is not None and self.design_parameters_doe is not None:
            raise ValueError(
                "Provide either 'conditions' (an explicit list of runs) or "
                "'design_parameters_doe' (a sampled design), not both."
            )
        if self.conditions is not None and not self.conditions:
            raise ValueError("'conditions' must contain at least one run.")
        return self


class SimulationResponse(BaseModel):
    """Response model for simulation creation."""

    simulation_id: str = Field(description="Simulation UUID")
    job_id: str = Field(description="Job UUID")
    protocol_id: str | None = Field(
        default=None,
        description="The saved protocol the simulation ran. For an inline "
        "protocol this is the protocol it resolved to: an existing one with the "
        "same content in the project, or a newly saved one.",
    )
    already_existed: bool = Field(
        default=False,
        description="True when an identical run (same model, protocol inputs, "
        "design and options) already existed, finished or still running, and "
        "was returned instead of starting a new one.",
    )


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


def expected_doe_simulation_count(doe: DesignParametersDOE) -> int:
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


#: The most ids ``POST /simulations/statuses`` returns per request.
_STATUSES_PAGE_SIZE = 1000


def _describe_config_errors(error: ValidationError, prefix: tuple = ()) -> str:
    """Turn a config ``ValidationError`` into one readable line per problem.

    Pydantic's own rendering echoes the whole input back, which buries the
    problem and can be long for an inline protocol. ``prefix`` is the config
    key the validated value sat under, when it was validated on its own.
    """
    problems = []
    unknown_top_level_key = False
    for item in error.errors():
        loc = (*prefix, *item["loc"])
        location = ".".join(str(part) for part in loc)
        if item["type"] == "extra_forbidden":
            problems.append(f"Unknown config key '{location}'.")
            unknown_top_level_key |= len(loc) == 1
        else:
            message = item["msg"].removeprefix("Value error, ")
            problems.append(f"{location}: {message}" if location else message)
    if unknown_top_level_key:
        valid = ", ".join(sorted(ProtocolSimulationBatchRequest.model_fields))
        problems.append(f"Valid top-level keys: {valid}.")
    return "Invalid simulation config: " + " ".join(problems)


def _parse_parameters(
    experiment_parameters: dict[str, Any],
    conditions: list[SimulationCondition] | None,
) -> dict[str, Any]:
    """Experiment parameters to parse an inline protocol into a template with.

    Parsing only adds the initial temperature/SOC ``input[...]`` references for
    keys it is given, so a key set only inside conditions must reach it too, or
    those runs silently start from the protocol's own initial state.
    """
    merged: dict[str, Any] = {}
    for condition in conditions or []:
        for name, value in condition.experiment_parameters.items():
            merged.setdefault(name, value)
    return {**merged, **experiment_parameters}


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
            - protocol_id: id of a saved protocol, in place of
              ``protocol_experiment`` (provide exactly one)
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
        if "conditions" in config:
            raise ValueError(
                "protocol() runs one simulation and does not take 'conditions'. "
                "Set its inputs with 'experiment_parameters' and "
                "'design_parameters', or use protocol_batch() for a list of runs."
            )
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
            try:
                doe = DesignParametersDOE.model_validate(existing_doe)
            except ValidationError as e:
                raise ValueError(
                    _describe_config_errors(e, prefix=("design_parameters_doe",))
                ) from None
        else:
            doe = None

        if doe is not None:
            expected = expected_doe_simulation_count(doe)
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
        (``POST /simulations/with-template/batch``). A saved ``protocol_id`` is
        already a template, so it skips the parse step.

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
            - protocol_id: id of a saved protocol, in place of
              ``protocol_experiment`` (provide exactly one)
            - design_parameters_doe: DesignParametersDOE dict
            - conditions: Optional list of ``{"experiment_parameters": {...},
              "design_parameters": {...}}`` dicts — one simulation per entry,
              in place of ``design_parameters_doe``. Top-level
              ``experiment_parameters`` fill in whatever a condition omits.
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
        if "design_parameters" in config:
            raise ValueError(
                "protocol_batch() does not take 'design_parameters', which sets one "
                "run's inputs in protocol(). Give each run its own with "
                "'conditions', or sweep them with 'design_parameters_doe'."
            )
        try:
            validated_config = ProtocolSimulationBatchRequest(**config)
        except ValidationError as e:
            raise ValueError(_describe_config_errors(e)) from None

        experiment_parameters = validated_config.experiment_parameters or {}

        if validated_config.protocol_id is not None:
            # No client-default fallback: the backend uses the template's own
            # project, which the client default need not match.
            template_id: str = validated_config.protocol_id
            project_id = validated_config.project_id
        else:
            # The parsed protocol becomes a project-scoped template, so the
            # backend requires a project here.
            protocol_experiment = cast(
                ProtocolExperimentConfig, validated_config.protocol_experiment
            )
            project_id = resolve_project_id(self.client, validated_config.project_id)
            parse_response = self.client.post(
                "/protocols/parse-to-template",
                json_payload={
                    "protocol_experiment": protocol_experiment.model_dump(),
                    "experiment_parameters": _parse_parameters(
                        experiment_parameters, validated_config.conditions
                    ),
                    "project_id": project_id,
                },
            )
            template_id = parse_response["template_id"]

        # Step 2: run the batch against the resolved template
        batch_payload: dict[str, Any] = {
            "parameterized_model": _normalize_parameterized_model(
                validated_config.parameterized_model
            ),
            "experiment_template_id": template_id,
            "experiment_parameter_sets": [experiment_parameters],
            "conditions": [
                {
                    "experiment_parameters": {
                        **experiment_parameters,
                        **condition.experiment_parameters,
                    },
                    "design_parameters": condition.design_parameters,
                }
                for condition in validated_config.conditions
            ]
            if validated_config.conditions
            else None,
            "design_parameters_doe": validated_config.design_parameters_doe.model_dump(
                exclude_none=True
            )
            if validated_config.design_parameters_doe
            else None,
            "max_backward_jumps": validated_config.max_backward_jumps,
            "study_id": validated_config.study_id,
            "extra_variables": validated_config.extra_variables,
            # Scopes a quick model's parameterized model to the project; the
            # backend rejects a quick model that has no project to belong to.
            "project_id": project_id,
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
        return [
            SimulationResponse(**{**item, "protocol_id": template_id})
            for item in response_data
        ]

    def list(
        self,
        parameterized_model_id: str | None = None,
        study_id: str | None = None,
        *,
        protocol_id: str | None = None,
        state: Literal["queued", "running", "completed", "failed"] | None = None,
        summary: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]] | PaginatedList[dict[str, Any]]:
        """List simulations of a parameterized model, a study, or a protocol.

        Scope by at most one of ``parameterized_model_id`` or ``study_id``,
        optionally narrowed to one saved protocol with ``protocol_id``; or by
        ``protocol_id`` alone. Newest first.

        Parameters
        ----------
        parameterized_model_id : str, optional
            Filter simulations belonging to this parameterized model.
        study_id : str, optional
            Filter simulations assigned to this study.
        protocol_id : str, optional
            Filter simulations that ran this saved protocol. Combines with
            either of the other two, e.g. every run of one model on one protocol.
        state : {"queued", "running", "completed", "failed"}, optional
            Only runs in this state. ``failed`` includes canceled runs.
        summary : bool, optional
            Leave out each run's ``metrics``, which are most of a listing's
            size. Defaults to False.
        limit : int, optional
            Maximum simulations to return (1-1000). The server defaults to 100.
        offset : int, optional
            Number of simulations to skip.

        Returns
        -------
        list[dict[str, Any]] | PaginatedList[dict[str, Any]]
            Simulation summaries. A :class:`PaginatedList` carrying ``.total``
            when ``limit`` or ``offset`` is given, otherwise a plain list.

        Raises
        ------
        ValueError
            If both ``parameterized_model_id`` and ``study_id`` are given, or
            none of the three scopes is.
        """
        if parameterized_model_id and study_id:
            raise ValueError(
                "Pass at most one of 'parameterized_model_id' or 'study_id'; "
                "'protocol_id' may be combined with either."
            )
        if not (parameterized_model_id or study_id or protocol_id):
            raise ValueError(
                "Provide 'parameterized_model_id', 'study_id' or 'protocol_id'."
            )
        endpoint = _build_endpoint(
            "/simulations",
            {
                "parameterized_model_id": parameterized_model_id,
                "study_id": study_id,
                "experiment_template_id": protocol_id,
                "state": state,
                "summary": "true" if summary else None,
                "limit": limit,
                "offset": offset,
            },
        )
        # The endpoint returns {simulations: [...], model_scalar_parameters: {...}}
        page = _parse_list_response(self.client.get(endpoint), dict, key="simulations")
        if limit is None and offset is None:
            return page.items
        return page

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

    def delete(self, simulation_id: str) -> None:
        """Delete a simulation and its stored result data.

        Parameters
        ----------
        simulation_id : str
            The UUID of the simulation to delete.
        """
        self.client.delete(f"/simulations/{simulation_id}")

    def get_statuses(self, simulation_ids: list[str]) -> list[dict[str, Any]]:
        """Get the job status of many simulations in one lightweight request.

        Much cheaper than calling :meth:`get` per simulation when polling a
        batch: only the status of each simulation's job is returned, not the
        joined simulation record.

        Parameters
        ----------
        simulation_ids : list[str]
            Ids of the simulations to check.

        Returns
        -------
        list[dict[str, Any]]
            One ``{"simulation_id", "status", "error", "error_code",
            "updated_at"}`` item per simulation that exists, is visible to the
            caller, and has a job. Ids that match none are omitted, so compare
            against the ids you asked for. ``status`` is the job status
            (``pending``, ``waiting``, ``processing``, ``completed``,
            ``failed`` or ``canceled``).
        """
        statuses: list[dict[str, Any]] = []
        for start in range(0, len(simulation_ids), _STATUSES_PAGE_SIZE):
            chunk = simulation_ids[start : start + _STATUSES_PAGE_SIZE]
            statuses.extend(
                self.client.post(
                    "/simulations/statuses",
                    json_payload={"simulation_ids": chunk, "limit": len(chunk)},
                )
            )
        return statuses

    def get_result(self, simulation_id: str) -> SimulationResult:
        """Get simulation data/result for a completed simulation.

        Downloads ``time_series.parquet`` and ``steps.parquet`` via signed-URL
        redirects, and loads ``metrics`` from the simulation record. Callers
        see the same :class:`SimulationResult` shape as before.

        Parameters
        ----------
        simulation_id : str
            The UUID of the simulation.

        Returns
        -------
        SimulationResult
            Typed result with ``time_series`` and ``steps`` as DataFrames and
            ``metrics`` as a plain dict. DataFrame type (polars or pandas)
            follows the active backend set via ``set_dataframe_backend()``.

        Raises
        ------
        IonworksError
            If the API request fails. A 404 typically means the result is not
            yet available (simulation still running, queued, or legacy data
            not yet upgraded to parquet). Other status codes indicate
            authentication failures, server errors, or a missing simulation
            ID.
        """
        base = f"/simulations/{simulation_id}"
        with ThreadPoolExecutor(max_workers=3) as pool:
            ts_fut = pool.submit(
                self.client.request_raw, "GET", f"{base}/time_series/download"
            )
            steps_fut = pool.submit(
                self.client.request_raw, "GET", f"{base}/steps/download"
            )
            sim_fut = pool.submit(self.get, simulation_id)
            time_series = ensure_time_series_capacity_columns(
                parquet_to_dataframe(ts_fut.result())
            )
            steps = ensure_steps_delta_columns(parquet_to_dataframe(steps_fut.result()))
            simulation = sim_fut.result()

        metrics = simulation.get("metrics") or {}
        return SimulationResult(
            time_series=time_series,
            steps=steps,
            metrics=metrics,
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
