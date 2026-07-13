"""Pydantic models for the Ionworks API client.

These models use extra="allow" to accept any fields from the API response,
letting the API handle validation. Required fields are kept minimal.
"""

from __future__ import annotations

from collections.abc import Iterator
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


# --- Cell Specification Models --- #


class CellSpecification(BaseModel):
    """Cell specification model - accepts any fields from the API.

    The API returns nested component/material data and ratings objects.
    This model is permissive to allow the API to define the schema.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


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
