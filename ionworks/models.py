"""Pydantic models for the Ionworks API client.

These models use extra="allow" to accept any fields from the API response,
letting the API handle validation. Required fields are kept minimal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, field_validator

from .validators import DataFrame, dict_to_df_validator

if TYPE_CHECKING:
    from .errors import IonworksError

_T = TypeVar("_T")


class PaginatedList(Generic[_T]):  # noqa: UP046 - needs Python 3.11 compat
    """A list-like container that also carries pagination metadata.

    Returned by ``list()`` methods when ``limit`` or ``offset`` is provided.
    Behaves like a regular ``list`` for iteration, indexing, truthiness, and
    ``len()`` so callers can treat it interchangeably with ``list[T]``.

    Parameters
    ----------
    items : list[_T]
        The page of results.
    total : int
        Total number of matching records across all pages.
    """

    __slots__ = ("items", "total")

    def __init__(self, items: list[_T], total: int) -> None:
        self.items = items
        self.total = total

    @property
    def count(self) -> int:
        """Number of items in this page (same as ``len(self)``)."""
        return len(self.items)

    def __iter__(self) -> Iterator[_T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int | slice) -> Any:
        return self.items[index]

    def __bool__(self) -> bool:
        return bool(self.items)

    def __repr__(self) -> str:
        return (
            f"PaginatedList(items={self.items!r}, count={self.count}, "
            f"total={self.total})"
        )


def _build_endpoint(base: str, params: dict[str, str | int | bool | None]) -> str:
    """Append non-None query parameters to a base endpoint path."""
    filtered = {k: str(v) for k, v in params.items() if v is not None}
    if not filtered:
        return base
    return f"{base}?{urlencode(filtered)}"


def _parse_list_response(  # noqa: UP047
    response_data: Any,
    model_class: type[_T],
) -> PaginatedList[_T]:
    """Parse a list endpoint response into model instances.

    Always returns a :class:`PaginatedList` which behaves like a regular
    ``list`` (iteration, indexing, ``len``, truthiness) so existing callers
    are unaffected. Also exposes ``.count`` and ``.total`` for pagination.

    Handles both the paginated dict format ``{"items": [...], "count": N,
    "total": N}`` and legacy plain-array responses for backward
    compatibility.

    Parameters
    ----------
    response_data : Any
        Raw response from the API (list or paginated dict).
    model_class : type[_T]
        The Pydantic model class to instantiate per item.

    Returns
    -------
    PaginatedList[_T]
        A list-like result with ``.items``, ``.count``, and ``.total``.
    """
    if isinstance(response_data, dict) and "items" in response_data:
        items = [model_class(**item) for item in response_data["items"]]
        return PaginatedList(
            items=items,
            total=response_data["total"],
        )
    # Legacy plain-array response (backward compat)
    items = [model_class(**item) for item in response_data]
    return PaginatedList(items=items, total=len(items))


def _build_filter_params(
    *,
    name: str | None = None,
    name_exact: str | None = None,
    created_by_email: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    order_by: str | None = None,
    order: str | None = None,
) -> dict[str, str]:
    """Translate Pythonic filter kwargs to backend query parameters.

    Converts user-friendly parameter names to the operator syntax expected by
    the backend API. For example, ``name="graphite"`` becomes
    ``name=ilike.%graphite%`` (a case-insensitive contains match).

    Parameters
    ----------
    name : str | None
        Case-insensitive substring match on the name field.
    name_exact : str | None
        Exact match on the name field. Takes precedence over ``name`` if both
        are provided.
    created_by_email : str | None
        Case-insensitive substring match on the creator's email.
    created_after : str | None
        ISO datetime string; return only records created after this time.
    created_before : str | None
        ISO datetime string; return only records created before this time.
    updated_after : str | None
        ISO datetime string; return only records updated after this time.
    updated_before : str | None
        ISO datetime string; return only records updated before this time.
    order_by : str | None
        Column to sort results by (e.g. ``"name"``, ``"created_at"``).
    order : str | None
        Sort direction: ``"asc"`` or ``"desc"``.

    Returns
    -------
    dict[str, str]
        Query parameters ready to pass to :func:`_build_endpoint`.
    """
    params: dict[str, str] = {}
    if name_exact is not None:
        params["name"] = f"eq.{name_exact}"
    elif name is not None:
        params["name"] = f"ilike.%{name}%"
    if created_by_email is not None:
        params["created_by_email"] = f"ilike.%{created_by_email}%"
    if created_after is not None:
        params["created_at_gt"] = created_after
    if created_before is not None:
        params["created_at_lt"] = created_before
    if updated_after is not None:
        params["updated_at_gt"] = updated_after
    if updated_before is not None:
        params["updated_at_lt"] = updated_before
    if order_by is not None:
        params["order_by"] = order_by
    if order is not None:
        params["order"] = order
    return params


def _extract_existing_id(e: IonworksError) -> str | None:
    """Extract ``existing_id`` from a CONFLICT error's detail payload.

    The standardized error format nests the ID under
    ``{"detail": {"existing_id": "..."}}``. Returns ``None`` when the
    field is missing or the payload has an unexpected shape.
    """
    if e.data is None:
        return None
    detail = e.data.get("detail", {})
    return detail.get("existing_id") if isinstance(detail, dict) else None


def create_or_get(  # noqa: UP047
    *,
    create: Callable[[], _T],
    get_by_id: Callable[[str], _T],
    find_by_name: Callable[[str], _T | None],
    name: str | None,
    resource_label: str,
) -> _T:
    """Create a resource, or return the existing one on a name conflict.

    Shared conflict-resolution used by the equipment sub-clients. Calls
    ``create``; on an ``IonworksError`` that is a CONFLICT (``error_code ==
    "CONFLICT"`` or HTTP 409), resolves the existing resource — first by the
    ``existing_id`` echoed in the error detail, then by ``find_by_name`` as a
    fallback. Any non-conflict error propagates unchanged.

    Parameters
    ----------
    create : Callable[[], _T]
        Zero-arg thunk that performs the create and returns the new resource.
    get_by_id : Callable[[str], _T]
        Fetch a resource by id (used with the conflict's ``existing_id``).
    find_by_name : Callable[[str], _T | None]
        Look up the existing resource by its (conflicting) name; returns
        ``None`` if not found.
    name : str | None
        The name that was being created, used for the fallback lookup and the
        error message.
    resource_label : str
        Human-readable resource name for the "duplicate but not found" error
        (e.g. ``"Cycler"``).

    Returns
    -------
    _T
        The newly created resource, or the pre-existing one on conflict.

    Raises
    ------
    ValueError
        If the create reported a duplicate but the existing resource could not
        be resolved by id or name.
    """
    # Imported lazily to avoid a circular import at module load time.
    from .errors import IonworksError

    try:
        return create()
    except IonworksError as e:
        if e.error_code == "CONFLICT" or e.status_code == 409:
            existing_id = _extract_existing_id(e)
            if existing_id:
                return get_by_id(existing_id)
            if name:
                found = find_by_name(name)
                if found is not None:
                    return found
            raise ValueError(
                f"{resource_label} '{name}' reported as duplicate but could "
                "not be found"
            ) from e
        raise


# --- Cell Specification Models --- #


class CellSpecification(BaseModel):
    """Cell specification model - accepts any fields from the API.

    The API returns nested component/material data and ratings objects.
    This model is permissive to allow the API to define the schema.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


# --- Equipment Models --- #


class Site(BaseModel):
    """Site model - accepts any fields from the API.

    A site is an organization-scoped physical location that owns cyclers.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


class Cycler(BaseModel):
    """Cycler model - accepts any fields from the API.

    A cycler is battery test equipment that belongs to a site, is owned by
    one project, and owns channels.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    site_id: str
    project_id: str


class Channel(BaseModel):
    """Channel model - accepts any fields from the API.

    A channel is an individual test channel belonging to a cycler; it
    inherits its cycler's project.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    cycler_id: str
    project_id: str
    #: Free-text notes on the channel. Nullable.
    notes: str | None = None
    #: Whether the channel is deliberately out of service (broken /
    #: maintenance). Shown as out-of-service in the Lab view.
    out_of_commission: bool = False
    #: Rated maximum current in amps (A). Nullable = unrated.
    max_amps: float | None = None
    #: Rated voltage window in volts (V). Nullable = unrated.
    min_volts: float | None = None
    max_volts: float | None = None


# --- Lab view (occupancy) models --- #


class ChannelState(StrEnum):
    """Derived occupancy state of a channel in the lab view."""

    free = "free"
    occupied = "occupied"
    stale = "stale"
    out_of_commission = "out_of_commission"


class LabMeasurementSummary(BaseModel):
    """The measurement occupying a channel (slim view for the lab wall)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    #: ISO time the test started, if known. Nullable.
    start_time: str | None = None
    #: ISO forecast finish time, if an estimate has been computed. Nullable.
    #: Informational only -- doesn't affect the derived occupancy state or the
    #: staleness window. The note explaining the estimate and the timestamp it
    #: was computed at aren't part of this slim summary; fetch the full
    #: measurement (``client.cell_measurement.get(measurement_id)``) for those.
    estimated_end_time: str | None = None
    #: ISO time the measurement was last updated (drives staleness).
    updated_at: str
    cell_instance_id: str
    cell_instance_name: str | None = None
    protocol_name: str | None = None


class LabChannel(BaseModel):
    """A channel with its derived occupancy state."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    state: ChannelState
    #: The measurement driving the state (occupied/stale); None when free/OOC.
    measurement: LabMeasurementSummary | None = None
    notes: str | None = None
    out_of_commission: bool = False
    max_amps: float | None = None
    min_volts: float | None = None
    max_volts: float | None = None


class LabCycler(BaseModel):
    """A cycler with its channels and per-cycler occupancy counts."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    manufacturer: str | None = None
    model: str | None = None
    channel_count: int
    occupied: int
    stale: int
    free: int
    out_of_commission: int = 0
    channels: list[LabChannel] = []


class LabSite(BaseModel):
    """A site grouping its cyclers for the lab wall."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    cyclers: list[LabCycler] = []


class LabStatus(BaseModel):
    """The full lab-view tree for a project plus project-level counts."""

    model_config = ConfigDict(extra="allow")

    sites: list[LabSite] = []
    occupied: int = 0
    stale: int = 0
    free: int = 0
    out_of_commission: int = 0


class FlatChannel(BaseModel):
    """A lab channel flattened with its parent cycler and site context.

    Returned by ``LabClient.free_channels`` / ``stale_channels`` so an answer
    reads as "CH3 on Maccor-1 at Boston Lab" without re-walking the tree.
    """

    channel: LabChannel
    cycler_id: str
    cycler_name: str
    site_id: str
    site_name: str


class Utilization(BaseModel):
    """Project lab utilization: the frontend headline percent plus raw counts.

    ``percent`` is the busy (occupied + stale) share of *all* channels
    (out-of-commission included in the denominator), rounded to a whole
    number to match the lab wall. ``0`` when there are no channels.
    """

    percent: int
    occupied: int
    stale: int
    free: int
    out_of_commission: int
    total: int


# --- Cell Instance Models --- #


class CellInstance(BaseModel):
    """Cell instance model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    cell_specification_id: str


# --- Cell Measurement Models --- #


class MeasurementType(StrEnum):
    """Type of data stored in a cell measurement."""

    time_series = "time_series"
    file = "file"
    properties = "properties"


class CellMeasurement(BaseModel):
    """Cell measurement model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    cell_instance_id: str
    measurement_type: MeasurementType = MeasurementType.time_series
    #: Optional channel the measurement was recorded on. Nullable. Only valid
    #: on ``time_series`` measurements, and requires ``start_time`` to be set.
    channel_id: str | None = None
    #: ISO-formatted time the test started. Nullable, but required when
    #: ``channel_id`` is set (it defines when the channel became occupied).
    start_time: str | None = None
    #: ISO-formatted time the test finished. Nullable — a null ``end_time``
    #: means the test is still running (e.g. in the lab equipment view). Set it
    #: once the measurement is complete. Must not precede ``start_time``.
    end_time: str | None = None
    #: ISO forecast finish time for a still-running test, if an estimate has
    #: been computed. Nullable. Distinct from ``end_time`` (the actual
    #: finish); informational only.
    estimated_end_time: str | None = None
    #: Free-text explanation of how ``estimated_end_time`` was derived.
    #: Nullable.
    estimated_end_time_note: str | None = None
    #: ISO-formatted time ``estimated_end_time`` was last computed. Nullable.
    estimated_end_time_calculated_at: str | None = None


# --- Bundle Models --- #


class CellMeasurementBundleResponse(CellMeasurement):
    """Flat response from creating a measurement bundle.

    Measurement fields (id, name, measurement_type, etc.) are at the top level
    alongside upload metadata.
    """

    steps_created: int


class UploadInfo(BaseModel):
    """Signed URL info for a single upload target."""

    filename: str | None = None
    signed_url: str
    token: str
    path: str


class InitiateUploadResponse(BaseModel):
    """Response from the initiate-upload endpoint for signed URL uploads."""

    measurement_id: str
    uploads: list[UploadInfo]


class InitiateRawDataUploadResponse(BaseModel):
    """Response from the raw-data initiate-upload endpoint."""

    raw_data_id: str
    uploads: list[UploadInfo]


# --- Detail Models --- #


class CellMeasurementDetail(CellMeasurement):
    """Flat detail model for a measurement with steps and time series.

    Measurement fields (id, name, measurement_type, etc.) are at the top level
    alongside optional data payloads. Returns minimal data by default: foreign
    keys for parent objects rather than nested objects. Use the spec/instance
    clients to fetch parent objects if needed.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    specification_id: str | None = None
    instance_id: str | None = None
    steps: DataFrame | None = None
    time_series: DataFrame | None = None
    cycles: DataFrame | None = None
    files: dict[str, bytes] | None = None

    @field_validator("steps", "time_series", "cycles", mode="before")
    @classmethod
    def convert_dict_to_df(cls, v: Any) -> Any:
        """Convert dictionary to DataFrame (polars or pandas based on config)."""
        return dict_to_df_validator(v)


class CellInstanceDetail(BaseModel):
    """Detail model for a cell instance with all measurements.

    Returns a foreign key for the parent specification rather
    than a nested object. Use ``client.cell_spec.get(detail
    .specification_id)`` to fetch the full specification.
    """

    instance: CellInstance
    specification_id: str
    measurements: list[CellMeasurementDetail]


# --- Equipment Detail Models --- #


class CyclerDetail(BaseModel):
    """Detail model for a cycler with all its channels.

    Returns a foreign key for the parent site rather than a nested object.
    Use ``client.site.get(detail.site_id)`` to fetch the full site.
    """

    cycler: Cycler
    site_id: str
    channels: list[Channel]


class SiteDetail(BaseModel):
    """Detail model for a site with all its cyclers.

    Each cycler is expanded to a :class:`CyclerDetail`, so its channels are
    included too.
    """

    site: Site
    cyclers: list[CyclerDetail]


# --- Material Models --- #


class ColumnSpec(BaseModel):
    """Column descriptor for a material property dataset."""

    model_config = ConfigDict(extra="allow")

    name: str
    unit: str = ""
    source_column_index: int


class MaterialPropertyDataset(BaseModel):
    """Material property dataset record as returned by the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    columns: list[ColumnSpec] = []
    data_version: int
    nan_counts: dict[str, int] | None = None
    created_at: str | None = None


class AnalysisType(StrEnum):
    """Well-known ``analysis_type`` values.

    Members are plain strings (``StrEnum``), so they can be passed directly
    wherever an ``analysis_type`` string is expected, e.g.
    ``client.analysis.create(..., analysis_type=AnalysisType.ECM_FROM_EIS)``.

    This set is **advisory, not exhaustive**: the API does not restrict
    ``analysis_type`` to these values — any non-empty string is accepted, so a
    new extractor can use a raw string without waiting for an SDK release.
    """

    ECM_FROM_EIS = "ecm_from_eis"
    LAM_LLI_FROM_RPT = "lam_lli_from_rpt"
    DCIR_FROM_HPPC = "dcir_from_hppc"


#: The well-known analysis-type string values as a plain list, for iteration or
#: display. Derived from :class:`AnalysisType`; prefer the enum for authoring.
KNOWN_ANALYSIS_TYPES: list[str] = [t.value for t in AnalysisType]


class AnalysisColumnSpec(BaseModel):
    """Column descriptor for an analysis parquet."""

    model_config = ConfigDict(extra="allow")

    name: str
    unit: str = ""
    dtype: str | None = None


class Analysis(BaseModel):
    """Analysis record as returned by the API.

    An analysis holds features extracted from a single ``cell_measurement``
    (e.g. ECM parameters from EIS, LLI/LAM from RPT), stored as a parquet
    table plus loose metadata.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    measurement_id: str
    project_id: str | None = None
    name: str
    analysis_type: str
    columns: list[AnalysisColumnSpec] = []
    metadata: dict = {}
    notes: str | None = None
    created_at: str | None = None


class Material(BaseModel):
    """Material record as returned by the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    manufacturer: str | None = None
    product_id: str | None = None


# --- Project Models --- #


class Project(BaseModel):
    """Project model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    organization_id: str


# --- Model (Custom Model) Models --- #


class Model(BaseModel):
    """Custom model model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    #: Model config (e.g. ``{"type": "SPMe"}``). The ``get`` response includes
    #: it; the ``create`` response may omit it, in which case this is ``None``.
    config: dict[str, Any] | None = None


# --- Parameterized Model Models --- #


class ParameterizedModel(BaseModel):
    """Parameterized model model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


# --- Raw Data Models --- #


class RawData(BaseModel):
    """A raw-data record.

    Represents an original uploaded file stored as-is, scoped to an
    organization and project. The API defines the full schema; extra fields
    are accepted.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    project_id: str
    name: str
    filename: str
    source: str | None = None


# --- Study Models --- #


class Study(BaseModel):
    """Study model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


# --- Optimization Models --- #


class Optimization(BaseModel):
    """Optimization model - accepts any fields from the API."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    job_id: str
    project_id: str


# --- Organization Usage Models --- #


class SimulationUsage(BaseModel):
    """Current simulation usage and its configured limit, in hours."""

    model_config = ConfigDict(extra="allow")

    #: Simulated battery time consumed this period, in hours.
    usage: float = 0
    #: Configured monthly limit in hours, or ``None`` when unconstrained.
    limit: float | None = None


class ComputeUsage(BaseModel):
    """Current compute usage and its configured limit, in hours.

    ``usage`` is the total across all job types; ``usage_by_type`` breaks it
    down by job type (informational — the ``limit`` applies to the total).
    """

    model_config = ConfigDict(extra="allow")

    #: Total backend compute time consumed this period, in hours.
    usage: float = 0
    #: Compute time per job type (``simulation``, ``datafit``, ``optimization``,
    #: ``validation``, ``pipeline``), in hours. Sums to ``usage``.
    usage_by_type: dict[str, float] = {}
    #: Configured monthly limit in hours, or ``None`` when unconstrained.
    limit: float | None = None


class OrganizationUsage(BaseModel):
    """An organization's usage and limits for the current billing period.

    Returned by :meth:`~ionworks.organization.OrganizationClient.usage`. Usage
    is aggregated across all members of the organization and resets on the first
    of each month. Simulation usage is a single figure; compute usage carries a
    per-job-type breakdown plus the total. All values are in hours. A ``None``
    limit means that usage type is unconstrained.
    """

    model_config = ConfigDict(extra="allow")

    #: Start of the current billing period (inclusive).
    period_start: datetime
    #: End of the current billing period (exclusive) — the next reset.
    period_end: datetime
    simulation: SimulationUsage = SimulationUsage()
    compute: ComputeUsage = ComputeUsage()


class StepsAndCycles(BaseModel):
    """Steps and cycle metrics for a measurement.

    Returned by the ``/steps_and_cycles`` endpoint which
    fetches both in one call (cycles are derived from steps).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    steps: DataFrame
    cycles: DataFrame

    @field_validator("steps", "cycles", mode="before")
    @classmethod
    def convert_dict_to_df(cls, v: Any) -> Any:
        """Convert dictionary to DataFrame."""
        return dict_to_df_validator(v)
