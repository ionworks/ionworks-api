"""
Cell instance client for managing individual cell records.

This module provides the :class:`CellInstanceClient` for creating, reading,
updating, and deleting cell instances, which represent individual physical
battery cells linked to a cell specification.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
import warnings

from .errors import IonworksError
from .models import (
    CellInstance,
    CellInstanceDetail,
    CellMeasurement,
    CellMeasurementDetail,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)


class CellInstanceClient:
    """Client for managing cell instances."""

    def __init__(self, client: Any) -> None:
        """Initialize the CellInstanceClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def get(self, cell_instance_id: str) -> CellInstance:
        """Get a specific cell instance by ID."""
        endpoint = f"/cell_instances/{cell_instance_id}"
        response_data = self.client.get(endpoint)
        return CellInstance(**response_data)

    def list(
        self,
        cell_spec_id: str,
        limit: int | None = None,
        offset: int | None = None,
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
    ) -> PaginatedList[CellInstance]:
        """List cell instances for a specification with optional filtering.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification.
        limit : int | None, optional
            Maximum number of instances to return per page.
        offset : int | None, optional
            Number of instances to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on instance name.
        name_exact : str | None, optional
            Exact match on instance name. Takes precedence over ``name``.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after : str | None, optional
            ISO datetime; return instances created after this time.
        created_before : str | None, optional
            ISO datetime; return instances created before this time.
        updated_after : str | None, optional
            ISO datetime; return instances updated after this time.
        updated_before : str | None, optional
            ISO datetime; return instances updated before this time.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[CellInstance]
            All instances belonging to the specification.
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
        endpoint = _build_endpoint(
            f"/cell_specifications/{cell_spec_id}/cell_instances",
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, CellInstance)

    def update(
        self,
        cell_instance_id: str,
        data: dict[str, Any],
    ) -> CellInstance:
        """Update an existing cell instance.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the cell instance to update.
        data : dict[str, Any]
            Dictionary containing the fields to update.

        Returns
        -------
        CellInstance
            The updated cell instance.
        """
        endpoint = f"/cell_instances/{cell_instance_id}"
        response_data = self.client.patch(endpoint, data)
        return CellInstance(**response_data)

    def delete(self, cell_instance_id: str) -> None:
        """Delete a cell instance by ID."""
        endpoint = f"/cell_instances/{cell_instance_id}"
        self.client.delete(endpoint)

    def detail(
        self,
        cell_instance_id: str,
        include_steps: bool = True,
        include_cycles: bool = True,
        include_time_series: bool = True,
    ) -> CellInstanceDetail:
        """Get full details for a cell instance.

        Composes flat API endpoints in parallel to build
        the complete detail view. For each measurement,
        fetches steps/cycles and time series according to
        the ``include_*`` flags.

        Parameters
        ----------
        cell_instance_id : str
            The cell instance ID.
        include_steps : bool, optional
            Whether to fetch step data for each measurement.
            Defaults to True.
        include_cycles : bool, optional
            Whether to fetch cycle metrics for each
            measurement. Defaults to True.
        include_time_series : bool, optional
            Whether to fetch time series for each
            measurement. Defaults to True.

        Returns
        -------
        CellInstanceDetail
            Instance metadata, specification foreign key,
            and a list of measurement details.
        """
        # Step 1: fetch instance + measurement list in
        # parallel
        with ThreadPoolExecutor(max_workers=2) as pool:
            inst_future = pool.submit(
                self.client.get,
                f"/cell_instances/{cell_instance_id}",
            )
            meas_future = pool.submit(
                self.client.get,
                f"/cell_instances/{cell_instance_id}/cell_measurements",
            )

        instance = CellInstance(**inst_future.result())
        measurements_meta = list(
            _parse_list_response(meas_future.result(), CellMeasurement)
        )

        # Step 2: for each measurement, fetch detail data
        # in parallel using the measurement client
        measurement_client = self.client.cell_measurement
        measurement_details: list[CellMeasurementDetail] = []

        if measurements_meta:
            with ThreadPoolExecutor(max_workers=min(len(measurements_meta), 6)) as pool:
                detail_futures = [
                    pool.submit(
                        measurement_client.detail,
                        m.id,
                        include_steps=include_steps,
                        include_cycles=include_cycles,
                        include_time_series=include_time_series,
                    )
                    for m in measurements_meta
                ]
            measurement_details = [f.result() for f in detail_futures]

        return CellInstanceDetail(
            instance=instance,
            specification_id=(instance.cell_specification_id),
            measurements=measurement_details,
        )

    def create(
        self,
        cell_spec_id: str,
        data: dict[str, Any],
    ) -> CellInstance:
        """Create a new cell instance under a specification.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the parent cell specification.
        data : dict[str, Any]
            Dictionary containing the cell instance data.

        Returns
        -------
        CellInstance
            The newly created cell instance.
        """
        endpoint = f"/cell_specifications/{cell_spec_id}/cell_instances"
        response_data = self.client.post(endpoint, data)
        return CellInstance(**response_data)

    def create_or_get(
        self,
        cell_spec_id: str,
        data: dict[str, Any],
    ) -> CellInstance:
        """Create a new cell instance or get existing.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the parent cell specification.
        data : dict[str, Any]
            Dictionary containing the cell instance data.

        Returns
        -------
        CellInstance
            The cell instance (newly created or existing).
        """
        try:
            return self.create(cell_spec_id, data)
        except IonworksError as e:
            if e.error_code == "CONFLICT" or e.status_code == 409:
                # Try to get existing instance by ID from error detail
                if e.data is not None:
                    detail = e.data.get("detail", {})
                    existing_id = (
                        detail.get("existing_id") if isinstance(detail, dict) else None
                    )
                    if existing_id:
                        return self.get(existing_id)
                    # Deprecated: legacy error format fallback
                    legacy_id = e.data.get("existing_cell_instance_id")
                    if legacy_id:
                        warnings.warn(
                            "Received legacy error key "
                            "'existing_cell_instance_id'. "
                            "Update the backend to use the "
                            "standardized error format.",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                        return self.get(legacy_id)
                # Fall back to looking the instance up by name. Names are
                # unique per specification, so filtering exactly within
                # ``cell_spec_id`` finds the row the constraint rejected.
                # Scanning ``self.list(cell_spec_id)`` instead would only see
                # the first page, so it missed the conflicting instance
                # whenever the spec held more than one page of them.
                instance_name = data.get("name")
                if instance_name:
                    matches = self.list(cell_spec_id, name_exact=instance_name)
                    if matches:
                        return matches[0]
                raise ValueError(
                    f"Cell instance '{instance_name}' reported as "
                    "duplicate but could not be found"
                ) from e
            raise
