"""Cell specification client for managing cell type definitions.

This module provides the :class:`CellSpecificationClient` for creating,
reading, updating, and deleting cell specifications, which define the
properties of battery cell types (manufacturer, chemistry, ratings, etc.).
"""

from __future__ import annotations

from typing import Any
import warnings

from ionworks.errors import IonworksError

from ._project_id import inject_project_id, resolve_project_id
from .models import (
    CellSpecification,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)

#: Component slots a cell spec can fill, in the order the API reports them.
#: The backend owns the canonical list (``SPEC_SLOTS``); the SDK can't import
#: backend code, so this is the package-boundary copy.
_SLOTS = ("anode", "cathode", "electrolyte", "separator", "case")

#: The per-slot material query params, derived from :data:`_SLOTS` so the slot
#: set has one owner within this module.
_MATERIAL_SLOT_PARAMS = tuple(f"{slot}_material_id" for slot in _SLOTS)


class CellSpecificationClient:
    """Client for managing cell specifications.

    Provides methods to create, read, update, and delete cell specifications,
    which define the properties of battery cell types (manufacturer, chemistry,
    ratings, etc.).
    """

    def __init__(self, client: Any) -> None:
        """Initialize the CellSpecificationClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def get(self, cell_spec_id: str) -> CellSpecification:
        """Get a specific cell specification by ID.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification to retrieve.

        Returns
        -------
        CellSpecification
            The requested cell specification object.
        """
        endpoint = f"/cell_specifications/{cell_spec_id}"
        response_data = self.client.get(endpoint)
        return CellSpecification(**response_data)

    def list(
        self,
        include_components: bool = False,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        form_factor: str | None = None,
        created_by_email: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
        project_id: str | None = None,
        material_id: str | list[str] | None = None,
        anode_material_id: str | None = None,
        cathode_material_id: str | None = None,
        electrolyte_material_id: str | None = None,
        separator_material_id: str | None = None,
        case_material_id: str | None = None,
        exclude_cell_spec_id: str | None = None,
    ) -> PaginatedList[CellSpecification]:
        """List cell specifications with optional pagination and filtering.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        include_components : bool, optional
            If True, returns each specification with its nested component and
            material data. Defaults to False (metadata only).
        limit : int | None, optional
            Maximum number of specs to return per page.
        offset : int | None, optional
            Number of specs to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on spec name.
        name_exact : str | None, optional
            Exact match on spec name. Takes precedence over ``name``.
        form_factor : str | None, optional
            Exact match on form factor.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after : str | None, optional
            ISO datetime; return specs created after this time.
        created_before : str | None, optional
            ISO datetime; return specs created before this time.
        updated_after : str | None, optional
            ISO datetime; return specs updated after this time.
        updated_before : str | None, optional
            ISO datetime; return specs updated before this time.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.
        project_id : str | None, optional
            Restrict results to this project. Required for a material
            reverse-lookup (see ``material_id``), where it falls back to
            ``IONWORKS_PROJECT_ID`` and raises if neither is set. For a plain
            list, omitting it applies no project filter.
        material_id : str | list[str] | None, optional
            Return specs referencing this material through any component. A
            single id matches specs using that material; a list matches specs
            using any of the given materials.
        anode_material_id : str | None, optional
            Return specs whose anode uses this material.
        cathode_material_id : str | None, optional
            Return specs whose cathode uses this material.
        electrolyte_material_id : str | None, optional
            Return specs whose electrolyte uses this material.
        separator_material_id : str | None, optional
            Return specs whose separator uses this material.
        case_material_id : str | None, optional
            Return specs whose case uses this material.
        exclude_cell_spec_id : str | None, optional
            Exclude the spec with this id from the results.

        Returns
        -------
        PaginatedList[CellSpecification]
            A list of cell specification objects.
        """
        filter_params = _build_filter_params(
            name=name,
            name_exact=name_exact,
            created_by_email=created_by_email,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            order_by=order_by,
            order=order,
        )
        if form_factor is not None:
            filter_params["form_factor"] = f"eq.{form_factor}"

        if isinstance(material_id, list):
            # An explicitly empty list means "match none of these materials". The
            # query string can't carry an empty repeated param, so the request
            # would drop the filter and return every spec — answer it here instead.
            if not material_id:
                return PaginatedList(items=[], total=0)
            filter_params["material_ids"] = material_id
        elif material_id is not None:
            filter_params["material_id"] = material_id
        # strict=True keeps the arg tuple aligned with _MATERIAL_SLOT_PARAMS: adding
        # a slot without its argument here fails loudly rather than dropping it.
        per_slot = dict(
            zip(
                _MATERIAL_SLOT_PARAMS,
                (
                    anode_material_id,
                    cathode_material_id,
                    electrolyte_material_id,
                    separator_material_id,
                    case_material_id,
                ),
                strict=True,
            )
        )
        for name_, val in (
            *per_slot.items(),
            ("exclude_cell_spec_id", exclude_cell_spec_id),
        ):
            if val is not None:
                filter_params[name_] = val

        # A material reverse-lookup is always project-scoped: materials and
        # components are deduplicated per project, so a cross-project match is
        # meaningless. project_id comes from the argument or IONWORKS_PROJECT_ID —
        # there is no org-wide material lookup and no silent fallback to unscoped.
        is_material_lookup = any(
            k in filter_params
            for k in ("material_id", "material_ids", *_MATERIAL_SLOT_PARAMS)
        )
        if is_material_lookup:
            filter_params["project_id"] = resolve_project_id(self.client, project_id)
        elif project_id is not None:
            filter_params["project_id"] = project_id

        endpoint = _build_endpoint(
            "/cell_specifications",
            {
                "full": "true" if include_components else None,
                "limit": limit,
                "offset": offset,
                **filter_params,
            },
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, CellSpecification)

    def related_specs(
        self,
        cell_spec_id: str,
        *,
        slots: list[str] | None = None,
        exclude_self: bool = True,
        limit: int = 100,
        offset: int = 0,
        include_components: bool = False,
    ) -> PaginatedList[CellSpecification]:
        """Other cell specs sharing a component material with this spec.

        Parameters
        ----------
        cell_spec_id : str
            The spec whose slot materials define the search.
        slots : list[str] | None, optional
            Slots to compare (subset of anode/cathode/electrolyte/separator/case).
            None → any slot: match other specs using any of this spec's slot
            materials in any slot. A list → per-slot: match each named slot only
            against this spec's material in that same slot (e.g. ``["anode",
            "cathode"]`` finds specs whose anode matches this anode *or* whose
            cathode matches this cathode).
        exclude_self : bool, optional
            Exclude ``cell_spec_id`` from results (applied server-side).
            Defaults True.
        limit : int, optional
            Maximum number of specs to return per page. Defaults to 100.
        offset : int, optional
            Number of specs to skip for pagination. Defaults to 0.
        include_components : bool, optional
            If True, return each spec with its nested component and material
            data. Defaults to False (metadata only).

        Returns
        -------
        PaginatedList[CellSpecification]
            Specs sharing a material, backend-paginated and self-excluded.
            Scoped to this spec's project; for a System-library spec (no
            project) the search spans the whole org.

        Raises
        ------
        ValueError
            If ``slots`` contains an unknown slot name.
        """
        search = list(slots) if slots is not None else list(_SLOTS)
        bad = [s for s in search if s not in _SLOTS]
        if bad:
            raise ValueError(f"Unknown slot(s) {bad}; allowed: {', '.join(_SLOTS)}")
        spec = self.get(cell_spec_id)
        mats_by_slot: dict[str, str] = {}
        for slot in search:
            comp = getattr(spec, slot, None)
            if isinstance(comp, dict):
                mat_id = comp.get("material_id")
            else:
                mat_id = getattr(comp, "material_id", None)
            if mat_id:
                mats_by_slot[slot] = mat_id
        if not mats_by_slot:
            return PaginatedList(items=[], total=0)
        # Scope to the spec's own project. `cell_specifications.project_id` is
        # NOT NULL, so a spec always has one — there is no org-wide variant of
        # this search to fall back to. CellSpecification is `extra="allow"`, so
        # the field is dynamic; a response missing it is a contract break worth
        # naming rather than silently widening the search.
        spec_project_id = getattr(spec, "project_id", None)
        if spec_project_id is None:
            raise IonworksError(
                f"Cell specification '{cell_spec_id}' has no project_id, so its "
                "related specs cannot be scoped to a project."
            )
        kwargs: dict[str, Any] = dict(
            limit=limit,
            offset=offset,
            include_components=include_components,
            project_id=spec_project_id,
        )
        if exclude_self:
            kwargs["exclude_cell_spec_id"] = cell_spec_id
        if slots is not None:
            # Explicit slots → per-slot params, so each slot matches only its
            # own material (no cross-slot mixing).
            for slot, mat_id in mats_by_slot.items():
                kwargs[f"{slot}_material_id"] = mat_id
        else:
            # Any-slot → union of this spec's slot materials across all slots.
            kwargs["material_id"] = list(dict.fromkeys(mats_by_slot.values()))
        return self.list(**kwargs)

    def create(self, data: dict[str, Any]) -> CellSpecification:
        """Create a new cell specification.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the cell specification data. If
            ``project_id`` is omitted, the client's default project_id is
            used.

        Returns
        -------
        CellSpecification
            The newly created cell specification object.
        """
        endpoint = "/cell_specifications"
        data = inject_project_id(self.client, data)
        response_data = self.client.post(endpoint, data)
        return CellSpecification(**response_data)

    def create_or_get(self, data: dict[str, Any]) -> CellSpecification:
        """Create a new cell specification or get an existing one.

        Creates a new cell specification if it doesn't exist, otherwise returns
        the existing one.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the cell specification data.

        Returns
        -------
        CellSpecification
            The cell specification object (newly created or existing).
        """
        try:
            return self.create(data)
        except IonworksError as e:
            if e.error_code == "CONFLICT" or e.status_code == 409:
                # Try to get existing spec by ID from error detail
                if e.data is not None:
                    detail = e.data.get("detail", {})
                    existing_id = (
                        detail.get("existing_id") if isinstance(detail, dict) else None
                    )
                    if existing_id:
                        return self.get(existing_id)
                    # Deprecated: legacy error format fallback
                    legacy_id = e.data.get("existing_cell_specification_id")
                    if legacy_id:
                        warnings.warn(
                            "Received legacy error key "
                            "'existing_cell_specification_id'. "
                            "Update the backend to use the "
                            "standardized error format.",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                        return self.get(legacy_id)
                # Fall back to listing and matching by name
                spec_name = data.get("name")
                if spec_name:
                    for spec in self.list():
                        if spec.name == spec_name:
                            return spec
                raise ValueError(
                    f"Cell specification '{spec_name}' reported as "
                    "duplicate but could not be found"
                ) from e
            raise

    def update(self, cell_spec_id: str, data: dict[str, Any]) -> CellSpecification:
        """Update an existing cell specification.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports nested
            component/material data for upsert.

        Returns
        -------
        CellSpecification
            The updated cell specification object.
        """
        endpoint = f"/cell_specifications/{cell_spec_id}"
        response_data = self.client.patch(endpoint, data)
        return CellSpecification(**response_data)

    def delete(self, cell_spec_id: str) -> None:
        """Delete a cell specification by ID.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification to delete.
        """
        endpoint = f"/cell_specifications/{cell_spec_id}"
        self.client.delete(endpoint)
