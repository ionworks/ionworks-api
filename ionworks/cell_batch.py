"""Cell batch client for managing manufacturing lots.

This module provides the :class:`CellBatchClient` for creating, reading,
updating, and deleting cell batches. A batch is a manufacturing lot — one
production run of one cell specification — that cell instances join through
their ``batch_id``.
"""

from __future__ import annotations

from typing import Any

from .models import (
    CellBatch,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
    create_or_get,
)


class CellBatchClient:
    """Client for managing cell batches (manufacturing lots)."""

    def __init__(self, client: Any) -> None:
        """Initialize the CellBatchClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def get(self, batch_id: str) -> CellBatch:
        """Get a specific cell batch by ID.

        Parameters
        ----------
        batch_id : str
            The ID of the cell batch.

        Returns
        -------
        CellBatch
            The cell batch, including its current ``instance_count``.
        """
        endpoint = f"/cell_batches/{batch_id}"
        response_data = self.client.get(endpoint)
        return CellBatch(**response_data)

    def list(
        self,
        cell_spec_id: str,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
    ) -> PaginatedList[CellBatch]:
        """List the batches of a cell specification.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification whose batches to list.
        limit : int | None, optional
            Maximum number of batches to return per page.
        offset : int | None, optional
            Number of batches to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on batch name.
        name_exact : str | None, optional
            Exact match on batch name. Takes precedence over ``name``.

        Returns
        -------
        PaginatedList[CellBatch]
            One page of batches belonging to the specification, with ``.total``
            for the full count.
        """
        filter_params = _build_filter_params(name=name, name_exact=name_exact)
        endpoint = _build_endpoint(
            f"/cell_specifications/{cell_spec_id}/cell_batches",
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, CellBatch)

    def create(
        self,
        cell_spec_id: str,
        data: dict[str, Any],
    ) -> CellBatch:
        """Create a new batch under a cell specification.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification the lot was manufactured to.
        data : dict[str, Any]
            Batch fields. ``name`` is required and must be unique within the
            specification (compared case-insensitively). Optional: ``notes``
            (str), ``date_manufactured`` (ISO date, ``"YYYY-MM-DD"``), and
            ``properties`` (dict; numeric values use the Quantity format
            ``{"value": ..., "unit": ...}``).

        Returns
        -------
        CellBatch
            The newly created batch.

        Raises
        ------
        IonworksError
            With ``error_code == "CONFLICT"`` (HTTP 409) if the specification
            already has a batch of that name; ``detail["existing_id"]`` names
            it. Use :meth:`create_or_get` to resolve that automatically.

        Examples
        --------
        Create a lot, then file a new cell under it::

            batch = client.cell_batch.create(
                spec.id,
                {"name": "LOT-2025-Q1", "date_manufactured": "2025-01-15"},
            )
            client.cell_instance.create(
                spec.id, {"name": "Cell 001", "batch_id": batch.id}
            )
        """
        endpoint = f"/cell_specifications/{cell_spec_id}/cell_batches"
        response_data = self.client.post(endpoint, data)
        return CellBatch(**response_data)

    def create_or_get(
        self,
        cell_spec_id: str,
        data: dict[str, Any],
    ) -> CellBatch:
        """Create a new batch, or get the existing one on a name conflict.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification the lot was manufactured to.
        data : dict[str, Any]
            Batch fields, as for :meth:`create`. On a conflict the existing
            batch is returned unchanged; the other fields are not applied.

        Returns
        -------
        CellBatch
            The batch (newly created or existing).

        Raises
        ------
        ValueError
            If the create reported a duplicate but the existing batch could not
            be found by id or name.
        """
        name = data.get("name")

        def _find_by_name(batch_name: str) -> CellBatch | None:
            # Names are unique case-insensitively, so compare the same way: an
            # exact-match filter would miss "lot-a" when "LOT-A" holds the name.
            wanted = batch_name.casefold()
            for batch in self.list(cell_spec_id, name=batch_name):
                if batch.name.casefold() == wanted:
                    return batch
            return None

        return create_or_get(
            create=lambda: self.create(cell_spec_id, data),
            get_by_id=self.get,
            find_by_name=_find_by_name,
            name=name,
            resource_label="Cell batch",
        )

    def update(
        self,
        batch_id: str,
        data: dict[str, Any],
    ) -> CellBatch:
        """Update an existing cell batch.

        Parameters
        ----------
        batch_id : str
            The ID of the cell batch to update.
        data : dict[str, Any]
            The fields to change (partial update): any of ``name``, ``notes``,
            ``date_manufactured``, and ``properties``. Renaming a batch renames
            it for every member instance, since they reference it by id.

        Returns
        -------
        CellBatch
            The updated batch.
        """
        endpoint = f"/cell_batches/{batch_id}"
        response_data = self.client.patch(endpoint, data)
        return CellBatch(**response_data)

    def delete(self, batch_id: str) -> None:
        """Delete a cell batch by ID.

        Member cell instances are kept and unlinked (their ``batch_id`` becomes
        None); deleting a lot never deletes its cells.

        Parameters
        ----------
        batch_id : str
            The ID of the cell batch to delete.
        """
        endpoint = f"/cell_batches/{batch_id}"
        self.client.delete(endpoint)
