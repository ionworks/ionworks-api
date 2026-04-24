"""Study client for managing studies and their resource assignments.

This module provides the :class:`StudyClient` for creating, reading,
updating, and deleting studies, as well as assigning and removing
simulations and measurements from studies.
"""

from __future__ import annotations

from typing import Any

from .models import (
    PaginatedList,
    Study,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)


class StudyClient:
    """Client for managing studies within projects.

    Studies group related simulations and measurements for comparison and
    analysis. They belong to a project within an organization.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the StudyClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def _prefix(self, project_id: str) -> str:
        """Build the base URL prefix for study endpoints."""
        return f"/projects/{project_id}/studies"

    def get(
        self,
        project_id: str,
        study_id: str,
    ) -> Study:
        """Get a specific study by ID.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study to retrieve.

        Returns
        -------
        Study
            The requested study object.
        """
        endpoint = f"{self._prefix(project_id)}/{study_id}"
        response_data = self.client.get(endpoint)
        return Study(**response_data)

    def list(
        self,
        project_id: str,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Study]:
        """List studies within a project.

        Parameters
        ----------
        project_id : str
            The ID of the project to list studies for.
        limit : int | None, optional
            Maximum number of studies to return per page.
        offset : int | None, optional
            Number of studies to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on study name.
        name_exact : str | None, optional
            Exact match on study name.
        order_by : str | None, optional
            Column to sort by.
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Study]
            A list of study objects (includes ``simulation_count`` and
            ``validation_count`` via extra fields).
        """
        filter_params = _build_filter_params(
            name=name,
            name_exact=name_exact,
            order_by=order_by,
            order=order,
        )
        endpoint = _build_endpoint(
            self._prefix(project_id),
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Study)

    def create(
        self,
        project_id: str,
        data: dict[str, Any] | None = None,
    ) -> Study:
        """Create a new study.

        Parameters
        ----------
        project_id : str
            The ID of the project to create the study in.
        data : dict[str, Any]
            Dictionary containing the study data. Required fields: ``name``.
            Optional fields: ``description``.

        Returns
        -------
        Study
            The newly created study object.
        """
        endpoint = self._prefix(project_id)
        response_data = self.client.post(endpoint, data)
        return Study(**response_data)

    def update(
        self,
        project_id: str,
        study_id: str,
        data: dict[str, Any] | None = None,
    ) -> Study:
        """Update an existing study.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports ``name`` and
            ``description``.

        Returns
        -------
        Study
            The updated study object.
        """
        endpoint = f"{self._prefix(project_id)}/{study_id}"
        response_data = self.client.patch(endpoint, data)
        return Study(**response_data)

    def delete(
        self,
        project_id: str,
        study_id: str,
    ) -> None:
        """Delete a study by ID.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study to delete.
        """
        endpoint = f"{self._prefix(project_id)}/{study_id}"
        self.client.delete(endpoint)

    # --- Simulation assignments ---

    def assign_simulation(
        self,
        project_id: str,
        study_id: str,
        simulation_id: str,
    ) -> dict[str, Any]:
        """Assign a simulation to a study.

        This operation is idempotent -- assigning an already-assigned simulation
        returns the existing mapping.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study.
        simulation_id : str
            The ID of the simulation to assign.

        Returns
        -------
        dict[str, Any]
            The study-simulation mapping record.
        """
        endpoint = f"{self._prefix(project_id)}/{study_id}/simulations/{simulation_id}"
        return self.client.post(endpoint, {})

    def remove_simulation(
        self,
        project_id: str,
        study_id: str,
        simulation_id: str,
    ) -> None:
        """Remove a simulation from a study.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study.
        simulation_id : str
            The ID of the simulation to remove.
        """
        endpoint = f"{self._prefix(project_id)}/{study_id}/simulations/{simulation_id}"
        self.client.delete(endpoint)

    # --- Measurement assignments ---

    def assign_measurement(
        self,
        project_id: str,
        study_id: str,
        measurement_id: str,
    ) -> dict[str, Any]:
        """Assign a measurement to a study.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study.
        measurement_id : str
            The ID of the measurement to assign.

        Returns
        -------
        dict[str, Any]
            The study-measurement mapping record.
        """
        endpoint = (
            f"{self._prefix(project_id)}/{study_id}/measurements/{measurement_id}"
        )
        return self.client.post(endpoint, {})

    def list_measurements(
        self,
        project_id: str,
        study_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """List measurements assigned to a study with pagination.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study.
        limit : int | None, optional
            Maximum number of measurements to return per page.
        offset : int | None, optional
            Number of measurements to skip for pagination.

        Returns
        -------
        dict[str, Any]
            Paginated response with ``items``, ``count``, and ``total`` keys.
        """
        endpoint = _build_endpoint(
            f"{self._prefix(project_id)}/{study_id}/measurements",
            {"limit": limit, "offset": offset},
        )
        return self.client.get(endpoint)

    def remove_measurement(
        self,
        project_id: str,
        study_id: str,
        measurement_id: str,
    ) -> None:
        """Remove a measurement from a study.

        Parameters
        ----------
        project_id : str
            The ID of the project.
        study_id : str
            The ID of the study.
        measurement_id : str
            The ID of the measurement to remove.
        """
        endpoint = (
            f"{self._prefix(project_id)}/{study_id}/measurements/{measurement_id}"
        )
        self.client.delete(endpoint)
