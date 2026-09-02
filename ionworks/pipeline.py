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

import itertools
import re
import time
from typing import TYPE_CHECKING, Any

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    ValidationError,
    model_validator,
)

from ._project_id import resolve_env_project_id, resolve_project_id
from .errors import IonworksError
from .models import PaginatedList, _build_endpoint, _parse_list_response
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


#: Keys only a parameter estimator writes; alongside validation stats they mark
#: a fit that re-validated its best-fit params, not a Validation element.
_FIT_SIGNALS = frozenset(
    {"x", "fun", "costs", "samples", "chains", "log_pdfs", "method", "results"}
)


def _coerce_element_result(
    name: str,
    raw: "dict[str, Any] | None",
    client: Any,
    job_id: "str | None",
    *,
    element_type: str | None = None,
) -> "iws.BaseResults":
    """Decode a raw element-result dict into a typed ``ionworks_schema`` result.

    A ``type`` this client knows is expected to decode, so a failure raises
    :class:`~ionworks.errors.IonworksError`. A missing or unknown ``type`` is
    inferred from the element's kind and the fields present, degrading to
    :class:`ionworks_schema.BaseResults` — a new result type on the server must
    not break every fit result for a client that predates it.

    Overlay, trace and posterior are bound as lazy fetchers, so they cost a
    request only on first access.

    Parameters
    ----------
    name : str
        The element's name (used only for error messages).
    raw : dict or None
        The element's raw result dict. ``None`` / non-dict is treated as empty.
    client : Any
        The HTTP client, used to bind the lazy fetchers.
    job_id : str or None
        The element's job id. When ``None``, no fetchers are attached.
    element_type : str, optional
        The element's kind (``ElementType`` value, e.g. ``"Data Fit"``,
        ``"Validation"``). Drives dispatch when the raw dict has no ``type``.

    Returns
    -------
    ionworks_schema.BaseResults
        The typed result object.
    """
    import ionworks_schema as iws

    raw = raw if isinstance(raw, dict) else {}
    kind = (element_type or "").lower()

    cls: type
    raw_type = raw.get("type")
    if raw_type in iws.results._REGISTRY:
        # A discriminator this client knows should decode, so surface a mismatch
        # instead of reshaping it away.
        try:
            result = iws.from_config(raw)
        except Exception as exc:
            raise IonworksError(
                f"Could not decode the result of element '{name}' as "
                f"'{raw_type}': {exc}. The installed ionworks client and the "
                f"server disagree about this result type — upgrading "
                f"'ionworks' usually resolves it."
            ) from exc
        return _attach_element_fetchers(result, client, job_id, kind)

    # A data-fit re-validates its best-fit params, so it also carries
    # ``summary_stats``; only validation stats with no optimizer output are a Validation.
    is_validation_shape = (
        "validation_results" in raw or "summary_stats" in raw
    ) and not (raw.keys() & _FIT_SIGNALS)
    if "validation" in kind:
        cls = iws.ValidationResult
    elif "entry" in kind or "calculation" in kind:
        cls = iws.PassthroughResult
    elif "chains" in raw or "log_pdfs" in raw:
        cls = iws.PosteriorResult
    elif "x" in raw and "fun" in raw:
        cls = iws.OptimizationResult
    elif "x" in raw and "results" in raw:
        cls = iws.RegressionResult
    elif "x" in raw and "method" in raw:
        cls = iws.EnsembleResult
    elif is_validation_shape and "fit" not in kind:
        # No element type was passed (a validation-only SimplePipeline), and
        # ``parameter_values`` alone would otherwise claim this for the fit branch.
        cls = iws.ValidationResult
    elif "parameter_values" in raw or "fit" in kind:
        cls = iws.ParameterEstimatorResult
    else:
        cls = iws.BaseResults

    payload = {k: v for k, v in raw.items() if k != "type"}
    try:
        result = cls(**payload)
    except Exception:  # noqa: BLE001 — never crash on an unexpected shape
        result = iws.BaseResults(parameter_values=raw.get("parameter_values"))

    return _attach_element_fetchers(result, client, job_id, kind)


def _attach_element_fetchers(
    result: "iws.BaseResults",
    client: Any,
    job_id: "str | None",
    kind: str = "",
) -> "iws.BaseResults":
    """Bind lazy overlay/trace/posterior fetchers to a result, and return it.

    Both data-fit and validation results expose a model-vs-data ``overlay``;
    only data-fit results have an optimizer ``trace``/``posterior`` (decided by
    the element's kind and decoded type). No-op when the result is neither, when
    ``job_id`` is missing, or when ``client`` is ``None`` — binding a fetcher
    over a ``None`` client would only fail later, on first lazy access.
    """
    import ionworks_schema as iws

    is_datafit = "fit" in kind or isinstance(result, iws.ParameterEstimatorResult)
    is_validation = "validation" in kind or isinstance(result, iws.ValidationResult)
    if (not is_datafit and not is_validation) or client is None or not job_id:
        return result
    fetchers: dict[str, Any] = {}
    if is_datafit:
        fetchers["trace"] = lambda: client.job.get_parameter_trace(job_id)
        fetchers["posterior"] = lambda: client.job.get_posterior_samples(job_id)
    fetchers["overlay"] = lambda: _fetch_overlay(client, job_id)
    fetchers["series"] = lambda: _fetch_series(client, job_id)
    result.set_source(**fetchers)
    return result


def _fetch_overlay(client: Any, job_id: str) -> dict[str, Any]:
    """Assemble ``{objective: {"type", "plots"}}`` from the job's plot manifest.

    The manifest the job wrote alongside its plots says which objectives there
    are, how many plots each produced, and what kind of objective it was — the
    last of which decides how the plots are drawn and cannot be recovered from
    anywhere else, since an element's objectives are not kept on its row.

    Objectives the backend stored no plots for are omitted rather than carried as
    empty entries.

    Parameters
    ----------
    client : Any
        The HTTP client, used to fetch the job's metadata and plot data.
    job_id : str
        The job whose overlay to assemble.

    Returns
    -------
    dict[str, Any]
        ``{objective_name: {"type": ..., "plots": [...]}}`` for every objective
        with stored plots. Empty when the job has no metadata blob yet.
    """
    try:
        metadata = client.job.get_metadata(job_id)
    except IonworksError as exc:
        # No metadata blob yet (404) — no overlay to assemble, same graceful
        # empty return as the posterior fetcher.
        if exc.status_code == 404:
            return {}
        raise

    overlay: dict[str, Any] = {}
    for objective, (count, objective_type) in _manifest_objectives(metadata).items():
        plots = _fetch_objective_plots(client, job_id, objective, count)
        if plots:
            overlay[objective] = {"type": objective_type, "plots": plots}
    return overlay


def _fetch_series(client: Any, job_id: str) -> dict[str, Any]:
    """Assemble ``{objective: {source: {channel: [values]}}}`` of model-vs-data series.

    Reads the job's series one objective and source at a time — the objectives
    and their sources (``"optimal"`` / ``"baseline"``) come from
    ``get_series_channels``, then ``get_series`` per (objective, source) — so the
    cost is set by the objectives asked for rather than by the whole metadata
    blob. Unlike :func:`_fetch_overlay` (decimated plot data), this is
    full-resolution.

    Parameters
    ----------
    client : Any
        The HTTP client, used to fetch the job's series channels and series.
    job_id : str
        The job whose series to assemble.

    Returns
    -------
    dict[str, Any]
        ``{objective_name: {source: {channel_name: [values]}}}``, reading only
        the sources each objective actually has — so a baseline-only objective is
        read as ``"baseline"`` rather than queried for a missing ``"optimal"``.
        Empty when the job has recorded no series (or has no metadata blob yet).
    """
    try:
        channels = client.job.get_series_channels(job_id)
    except IonworksError as exc:
        # No series recorded yet (404) — nothing to assemble, same graceful
        # empty return as the overlay fetcher.
        if exc.status_code == 404:
            return {}
        raise

    series: dict[str, Any] = {}
    for objective, sources in channels.items():
        by_source = {}
        for source in sources:
            source_series = client.job.get_series(job_id, objective, source=source)
            if source_series:
                by_source[source] = source_series
        if by_source:
            series[objective] = by_source
    return series


def _manifest_objectives(metadata: dict) -> "dict[str, tuple[int | None, str | None]]":
    """Read ``{objective: (plot_count, type)}`` out of a job's plot manifest.

    Two stored shapes: the current one keeps a manifest under ``objectives``
    because the plots live in storage, while older jobs kept the plots inline
    under each objective's own key. Either way the count is already there, so
    there is never a need to probe the plot endpoint for it. A job written before
    the type was recorded reports ``None`` and plots via the generic renderer.
    """
    plot_config = metadata.get("validation_plot_config") or {}
    if not isinstance(plot_config, dict):
        return {}
    entries = plot_config.get("objectives")
    if not isinstance(entries, dict):
        # Older inline format, or a job with only validation_results to go on.
        entries = plot_config or metadata.get("validation_results") or {}
    objectives: dict[str, tuple[int | None, str | None]] = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            objectives[name] = (None, None)
            continue
        count = entry.get("plot_count")
        if count is None and isinstance(entry.get("plots"), list):
            count = len(entry["plots"])
        objectives[name] = (count, entry.get("type"))
    return objectives


def _fetch_objective_plots(
    client: Any, job_id: str, objective: str, count: "int | None"
) -> "list[dict[str, Any]]":
    """Fetch one objective's stored plots, in order.

    An objective emits as many panels as it likes — the fitted curve, its
    differential, the residual — and the manifest says how many. Without a count
    (a job whose manifest predates it) the indices are walked until the endpoint
    reports there are no more.

    A 404 means that objective has no server-side plot data at all; treat it as
    absent rather than letting it propagate and break ``.overlay`` for every
    other objective.
    """
    indices = range(count) if count is not None else itertools.count()
    plots: list[dict[str, Any]] = []
    for index in indices:
        try:
            payload = client.job.get_plot_data(job_id, objective, index)
        except IonworksError as exc:
            if exc.status_code == 404:
                break
            raise
        if not isinstance(payload, dict) or not payload.get("traces"):
            break
        plots.append(payload)
    return plots


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
    """Complete response from retrieving pipeline results.

    ``result`` and ``element_results`` are the raw dicts returned by the
    backend, unchanged. :meth:`element` and :attr:`results` additionally decode
    those raw per-element dicts into typed ``ionworks_schema`` result objects
    (lazily, cached), wiring up overlay/trace/posterior fetchers per element.
    """

    result: dict[str, Any]
    element_results: dict[str, Any]

    # Back-refs the typed accessors need to look up per-element job ids and bind
    # lazy fetchers. Kept out of serialization: the raw dicts are the wire contract.
    _client: Any = PrivateAttr(default=None)
    _pipeline_id: str | None = PrivateAttr(default=None)
    _elements_cache: "list[dict[str, Any]] | None" = PrivateAttr(default=None)
    _results_cache: dict = PrivateAttr(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        """Build the response, accepting the back-refs as keyword arguments.

        Pydantic silently drops underscore-prefixed keyword arguments, so
        ``client`` / ``pipeline_id`` are popped here and assigned to their
        private attributes. Passing them at construction keeps a response from
        ever existing without them — the lazy fetchers no-op on a missing
        client, which would otherwise surface much later as an empty overlay.

        Parameters
        ----------
        ``**data``
            The backend's response fields, plus optional ``client`` (HTTP
            client) and ``pipeline_id``.
        """
        client = data.pop("client", None)
        pipeline_id = data.pop("pipeline_id", None)
        super().__init__(**data)
        self._client = client
        self._pipeline_id = pipeline_id

    def _get_elements(self) -> "list[dict[str, Any]]":
        """Fetch (and cache) the pipeline's elements listing for job-id lookup."""
        if self._elements_cache is None:
            if self._client is None or self._pipeline_id is None:
                raise ValueError(
                    "PipelineResponse has no client/pipeline context; typed "
                    "results are only available from PipelineClient.result()."
                )
            self._elements_cache = self._client.get(
                f"/pipelines/{self._pipeline_id}/elements"
            )
        return self._elements_cache

    def element(self, name: str) -> "iws.BaseResults":
        """Return the typed ``ionworks_schema`` result for a named element.

        Decodes ``element_results[name]`` into the matching iws result class
        (see :func:`_coerce_element_result`) and caches it. The overlay / trace
        / posterior fetchers are bound to the element's job.

        Parameters
        ----------
        name : str
            The element name (the key used in ``element_results``).

        Returns
        -------
        ionworks_schema.BaseResults
            The typed result object.

        Raises
        ------
        ValueError
            If no element with the given name exists in the pipeline.
        """
        if name in self._results_cache:
            return self._results_cache[name]
        for element in self._get_elements():
            if element.get("name") == name:
                result = _coerce_element_result(
                    name,
                    self.element_results.get(name),
                    self._client,
                    element.get("job_id"),
                    element_type=element.get("element_type"),
                )
                self._results_cache[name] = result
                return result
        raise ValueError(
            f"No element named '{name}' found in pipeline {self._pipeline_id}."
        )

    @property
    def results(self) -> "dict[str, iws.BaseResults]":
        """Typed results for every element, keyed by element name (cached)."""
        return {name: self.element(name) for name in self.element_results}


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

    def update(
        self,
        pipeline_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> PipelineSubmissionResponse:
        """Partially update a pipeline's name and/or description.

        This is a metadata-only update: it cannot modify the pipeline's config
        or affect a running job.

        Parameters
        ----------
        pipeline_id : str
            The pipeline ID to update.
        name : str | None, optional
            New name. Omit (or pass ``None``) to leave unchanged.
        description : str | None, optional
            New description. Omit (or pass ``None``) to leave unchanged.

        Returns
        -------
        PipelineSubmissionResponse
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

        response_data = self.client.patch(f"/pipelines/{pipeline_id}", payload)
        return PipelineSubmissionResponse(**response_data)

    def list(
        self,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        id: str | None = None,
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
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[PipelineSubmissionResponse]:
        """List pipelines for a project.

        Parameters
        ----------
        project_id : str | None, optional
            The project id to filter pipelines. If not provided, uses the
            project_id set on the Ionworks client or the
            IONWORKS_PROJECT_ID environment variable.
        limit : int | None, optional
            Maximum number of pipelines to return. If not provided, returns
            all pipelines (up to the API's default limit).
        offset : int | None, optional
            Number of pipelines to skip for pagination. Sent to the API as
            ``skip`` — the ``/pipelines`` route's wire name for this
            parameter — while keeping ``offset`` as the Python name for
            consistency with the SDK's other ``list()`` methods.
        id, name, description, status, created_by_email : str | None, optional
            Optional filters. Pass values directly for exact match or use
            Supabase operator syntax (e.g. ``"ilike.%foo%"``,
            ``"in.(completed,failed)"``).
        created_at, updated_at : str | None, optional
            Optional Supabase-operator filter on the timestamp field (e.g.
            ``"gte.2025-01-01"``).
        created_at_gt, created_at_lt, updated_at_gt, updated_at_lt :
            str | None, optional
            Inclusive lower / upper bounds for between-style date queries.
        order_by : str | None, optional
            Sort column. One of ``id``, ``name``, ``description``, ``status``,
            ``created_at``, ``updated_at``, ``created_by_email``. Falls back
            to the API's default (``created_at``).
        order : str | None, optional
            Sort direction (``"asc"`` or ``"desc"``). Falls back to the API's
            default (``"desc"``).

        Returns
        -------
        PaginatedList[PipelineSubmissionResponse]
            A list-like page of pipeline submission responses, with
            ``.count`` and ``.total`` for pagination.

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

        endpoint = _build_endpoint(
            "/pipelines",
            {
                "project_id": project_id,
                "limit": limit,
                "skip": offset,
                "id": id,
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
                "order_by": order_by,
                "order": order,
            },
        )
        try:
            response_data = self.client.get(endpoint)
            # This route nests its page under "pipelines" rather than the
            # "items" every other list endpoint uses.
            if not isinstance(response_data, list | dict) or (
                isinstance(response_data, dict) and "pipelines" not in response_data
            ):
                raise ValueError(
                    f"Unexpected response format from {endpoint}: expected a list or "
                    f"dict with 'pipelines' key, got {type(response_data).__name__}"
                )
            return _parse_list_response(
                response_data, PipelineSubmissionResponse, key="pipelines"
            )
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
        return PipelineResponse(**response_data, client=self.client, pipeline_id=job_id)

    def cancel(self, pipeline_id: str) -> PipelineSubmissionResponse:
        """Cancel a running pipeline and all its non-terminal elements.

        Parameters
        ----------
        pipeline_id : str
            The pipeline ID to cancel.

        Returns
        -------
        PipelineSubmissionResponse
            The updated record (status will be ``canceled`` if cancellation
            took effect; otherwise the current state is returned).
        """
        response_data = self.client.post(f"/pipelines/{pipeline_id}/cancel", {})
        return PipelineSubmissionResponse(**response_data)

    def delete(self, pipeline_id: str) -> None:
        """Delete a pipeline, its elements, associated jobs, and storage files.

        Parameters
        ----------
        pipeline_id : str
            The pipeline ID to delete.
        """
        self.client.delete(f"/pipelines/{pipeline_id}")

    def get_element_metadata(
        self,
        pipeline_id: str,
        element_name: str,
        elements: "list[dict[str, Any]] | None" = None,
    ) -> dict[str, Any]:
        """Fetch the metadata blob for a named element of a pipeline.

        Locates the element by name, then delegates to
        ``client.job.get_metadata``. Use it for fields stripped from
        ``element.result`` and persisted to storage instead — for example the
        ``validation_plot_config`` a validation element writes.

        Not for a validation element's time series: those are stored per
        objective rather than on this blob. Use
        :meth:`get_element_series_channels` and :meth:`get_element_series`,
        which cost one objective rather than all of them.

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
        job_id = self._element_job_id(pipeline_id, element_name, elements)
        return self.client.job.get_metadata(job_id)

    def _element_job_id(
        self,
        pipeline_id: str,
        element_name: str,
        elements: "list[dict[str, Any]] | None" = None,
    ) -> str:
        """The job id behind a named element."""
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
                return job_id
        raise ValueError(
            f"No element named '{element_name}' found in pipeline {pipeline_id}."
        )

    def get_element_series_channels(
        self,
        pipeline_id: str,
        element_name: str,
        elements: "list[dict[str, Any]] | None" = None,
    ) -> "dict[str, dict[str, list[str]]]":
        """List an element's validation-series objectives and their channels.

        Delegates to :meth:`~ionworks.job.JobClient.get_series_channels` for the element's
        job. Names only, no samples — call it before
        :meth:`get_element_series` to learn which objectives exist, which channels each
        carries, and which of the two solves it has.

        Parameters
        ----------
        pipeline_id : str
            The pipeline whose element to describe.
        element_name : str
            Name of the element within the pipeline.
        elements : list[dict], optional
            Pre-fetched elements list, as for :meth:`get_element_metadata`.

        Returns
        -------
        dict[str, dict[str, list[str]]]
            ``{objective: {source: [channel, ...]}}``.
        """
        job_id = self._element_job_id(pipeline_id, element_name, elements)
        return self.client.job.get_series_channels(job_id)

    def get_element_series(
        self,
        pipeline_id: str,
        element_name: str,
        objective: str,
        elements: "list[dict[str, Any]] | None" = None,
        **kwargs: Any,
    ) -> "dict[str, list]":
        """Get one objective's model-vs-data series from a named element.

        Delegates to :meth:`~ionworks.job.JobClient.get_series` for the element's job. This
        is the way to read a validation element's time series: it reads one objective rather
        than the element's whole metadata blob.

        Parameters
        ----------
        pipeline_id : str
            The pipeline whose element to read.
        element_name : str
            Name of the element within the pipeline.
        objective : str
            Objective name, as listed by :meth:`get_element_series_channels`.
        elements : list[dict], optional
            Pre-fetched elements list, as for :meth:`get_element_metadata`.
        **kwargs
            Passed through to :meth:`~ionworks.job.JobClient.get_series` — ``source``,
            ``columns``, ``x_column``, ``max_points``, ``x_min``, ``x_max``.

        Returns
        -------
        dict[str, list]
            One list per channel, all of equal length, keyed by channel name.
        """
        job_id = self._element_job_id(pipeline_id, element_name, elements)
        return self.client.job.get_series(job_id, objective, **kwargs)

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
