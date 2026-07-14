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
    ) -> PaginatedList[Material]:
        """List materials for the current organization.

        Parameters
        ----------
        limit : int | None, optional
            Maximum number of records to return.
        offset : int | None, optional
            Number of records to skip for pagination.
        project_id : str | None, optional
            When provided, scopes ``property_count`` on each material to this
            project only. Does not filter which materials are returned.

        Returns
        -------
        PaginatedList[Material]
            A paginated list of material records.
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if project_id is not None:
            params["project_id"] = project_id
        endpoint = _build_endpoint(self._BASE, params)
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Material)
