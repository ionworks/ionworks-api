"""Cell specification client for managing cell type definitions.

This module provides the :class:`CellSpecificationClient` for creating,
reading, updating, and deleting cell specifications, which define the
properties of battery cell types (manufacturer, chemistry, ratings, etc.).
"""

from __future__ import annotations

from typing import Any
import warnings

from ionworks.errors import IonworksError

from .models import (
    CellSpecification,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)


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

    def create(self, data: dict[str, Any]) -> CellSpecification:
        """Create a new cell specification.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the cell specification data.

        Returns
        -------
        CellSpecification
            The newly created cell specification object.
        """
        endpoint = "/cell_specifications"
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
