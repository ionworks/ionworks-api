"""Material client for reading materials within an organization.

This module provides :class:`MaterialClient` for listing and retrieving
materials. Materials represent physical substances (electrodes, electrolytes,
separators, etc.). Material property datasets are managed separately via
``client.material_property_dataset``.
"""

from __future__ import annotations

from typing import Any

from .models import Material, PaginatedList, _build_endpoint, _parse_list_response


class MaterialClient:
    """Client for reading materials within an organization.

    Access via ``client.material``.

    System materials (shared across all organizations) are included by default
    when listing.
    """

    _BASE = "/materials"

    def __init__(self, client: Any) -> None:
        """Initialize the MaterialClient.

        Parameters
        ----------
        client : Any
            The parent :class:`~ionworks.client.Ionworks` instance.
        """
        self.client = client

    def get(self, material_id: str) -> Material:
        """Retrieve a material by ID.

        Parameters
        ----------
        material_id : str
            The material UUID.

        Returns
        -------
        Material
            The material record.

        Raises
        ------
        IonworksError
            If the material is not found or not accessible.
        """
        endpoint = f"{self._BASE}/{material_id}"
        return Material(**self.client.get(endpoint))

    def list(
        self,
        limit: int | None = None,
        offset: int | None = None,
        *,
        project_id: str | None = None,
        name: str | None = None,
        name_exact: str | None = None,
        manufacturer: str | None = None,
        product_id: str | None = None,
        created_at: str | None = None,
        created_at_gt: str | None = None,
        created_at_lt: str | None = None,
        updated_at: str | None = None,
        updated_at_gt: str | None = None,
        updated_at_lt: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Material]:
        """List materials in a project.

        Materials are scoped to a single project, so ``project_id`` is
        required — it falls back to the client's default when omitted.

        Filtering happens server-side. Text filters accept a bare value for
        exact equality or a PostgREST operator prefix for anything else, e.g.
        ``name="ilike.%graphite%"`` for a case-insensitive partial match. Use
        ``name_exact`` as a readable shorthand for exact equality.

        Parameters
        ----------
        limit : int | None, optional
            Maximum number of records to return (server default: 100).
        offset : int | None, optional
            Number of records to skip for pagination.
        project_id : str | None, optional
            Project to list materials from. Falls back to the client's default
            ``project_id`` when omitted.
        name : str | None, optional
            Filter on material name. Bare value matches exactly; prefix with
            an operator (e.g. ``"ilike.%NMC%"``) for a partial match.
        name_exact : str | None, optional
            Exact-match shorthand for ``name``. Cannot be combined with
            ``name``.
        manufacturer : str | None, optional
            Filter on manufacturer, same operator rules as ``name``.
        product_id : str | None, optional
            Filter on product id, same operator rules as ``name``.
        created_at : str | None, optional
            Filter on creation time, e.g. ``"gte.2026-01-01"``.
        created_at_gt : str | None, optional
            Lower bound for a creation-time range query.
        created_at_lt : str | None, optional
            Upper bound for a creation-time range query.
        updated_at : str | None, optional
            Filter on update time, same rules as ``created_at``.
        updated_at_gt : str | None, optional
            Lower bound for an update-time range query.
        updated_at_lt : str | None, optional
            Upper bound for an update-time range query.
        order_by : str | None, optional
            Column to sort by: ``"name"`` (server default), ``"manufacturer"``,
            ``"created_at"``, or ``"updated_at"``.
        order : str | None, optional
            Sort direction, ``"asc"`` (server default) or ``"desc"``.

        Returns
        -------
        PaginatedList[Material]
            A paginated list of material records.

        Raises
        ------
        ValueError
            If no ``project_id`` is provided and the client has no default, or
            if both ``name`` and ``name_exact`` are given.
        """
        project_id = project_id or self.client.project_id
        if not project_id:
            raise ValueError(
                "project_id is required or IONWORKS_PROJECT_ID must be set"
            )
        if name is not None and name_exact is not None:
            raise ValueError("pass either name or name_exact, not both")
        if name_exact is not None:
            name = f"eq.{name_exact}"

        params: dict[str, Any] = {"project_id": project_id}
        optional = {
            "limit": limit,
            "offset": offset,
            "name": name,
            "manufacturer": manufacturer,
            "product_id": product_id,
            "created_at": created_at,
            "created_at_gt": created_at_gt,
            "created_at_lt": created_at_lt,
            "updated_at": updated_at,
            "updated_at_gt": updated_at_gt,
            "updated_at_lt": updated_at_lt,
            "order_by": order_by,
            "order": order,
        }
        params.update({k: v for k, v in optional.items() if v is not None})
        endpoint = _build_endpoint(self._BASE, params)
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Material)
