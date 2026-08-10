"""Study client for managing studies and their resource assignments.

This module provides the :class:`StudyClient` for creating, reading,
updating, and deleting studies, as well as assigning and removing
simulations and measurements from studies.
"""

from __future__ import annotations

from typing import Any

from ._project_id import resolve_project_id
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

    All methods accept ``project_id`` as an optional argument. When omitted,
    the value falls back to the ``project_id`` configured on the parent
    :class:`~ionworks.Ionworks` client (resolved from the
    ``IONWORKS_PROJECT_ID`` env var if not passed explicitly). Methods raise
    ``ValueError`` if no project_id is available from any source.
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
        study_id: str,
        project_id: str | None = None,
    ) -> Study:
        """Get a specific study by ID.

        Parameters
        ----------
        study_id : str
            The ID of the study to retrieve.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        Study
            The requested study object.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{study_id}"
        response_data = self.client.get(endpoint)
        return Study(**response_data)

    def list(
        self,
        project_id: str | None = None,
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
        project_id : str | None, optional
            The ID of the project to list studies for. Defaults to the
            project_id set on the Ionworks client.
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
        project_id = resolve_project_id(self.client, project_id)
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
        data: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> Study:
        """Create a new study.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the study data. Required fields: ``name``.
            Optional fields: ``description``.
        project_id : str | None, optional
            The ID of the project to create the study in. Defaults to the
            project_id set on the Ionworks client.

        Returns
        -------
        Study
            The newly created study object.
        """
        if isinstance(data, str):
            # Common mistake: create(project_id, data) — the signature is
            # create(data, project_id=...). Fail fast with a clear message
            # rather than sending a string body and getting an opaque 422.
            raise ValueError(
                "study.create expects the study data dict as the first "
                "argument, but received a string. Call "
                "client.study.create({'name': ...}, project_id=...)."
            )
        if data is None or "name" not in data:
            raise ValueError(
                "study.create requires a data dict with at least a 'name' key, "
                "e.g. client.study.create({'name': 'My study'})."
            )
        project_id = resolve_project_id(self.client, project_id)
        endpoint = self._prefix(project_id)
        response_data = self.client.post(endpoint, data)
        return Study(**response_data)

    def update(
        self,
        study_id: str,
        data: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> Study:
        """Update an existing study.

        Parameters
        ----------
        study_id : str
            The ID of the study to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports ``name`` and
            ``description``.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        Study
            The updated study object.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{study_id}"
        response_data = self.client.patch(endpoint, data)
        return Study(**response_data)

    def delete(
        self,
        study_id: str,
        project_id: str | None = None,
    ) -> None:
        """Delete a study by ID.

        Parameters
        ----------
        study_id : str
            The ID of the study to delete.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{study_id}"
        self.client.delete(endpoint)

    # --- Simulation assignments ---

    def assign_simulation(
        self,
        study_id: str,
        simulation_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign a simulation to a study.

        This operation is idempotent -- assigning an already-assigned simulation
        returns the existing mapping.

        Parameters
        ----------
        study_id : str
            The ID of the study.
        simulation_id : str
            The ID of the simulation to assign.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        dict[str, Any]
            The study-simulation mapping record.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{study_id}/simulations/{simulation_id}"
        return self.client.post(endpoint, {})

    def remove_simulation(
        self,
        study_id: str,
        simulation_id: str,
        project_id: str | None = None,
    ) -> None:
        """Remove a simulation from a study.

        Parameters
        ----------
        study_id : str
            The ID of the study.
        simulation_id : str
            The ID of the simulation to remove.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{study_id}/simulations/{simulation_id}"
        self.client.delete(endpoint)

    # --- Measurement assignments ---

    def assign_measurement(
        self,
        study_id: str,
        measurement_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign a measurement to a study.

        Parameters
        ----------
        study_id : str
            The ID of the study.
        measurement_id : str
            The ID of the measurement to assign.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        dict[str, Any]
            The study-measurement mapping record.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = (
            f"{self._prefix(project_id)}/{study_id}/measurements/{measurement_id}"
        )
        return self.client.post(endpoint, {})

    def list_measurements(
        self,
        study_id: str,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """List measurements assigned to a study with pagination.

        Parameters
        ----------
        study_id : str
            The ID of the study.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.
        limit : int | None, optional
            Maximum number of measurements to return per page.
        offset : int | None, optional
            Number of measurements to skip for pagination.

        Returns
        -------
        dict[str, Any]
            Paginated response with ``items``, ``count``, and ``total`` keys.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = _build_endpoint(
            f"{self._prefix(project_id)}/{study_id}/measurements",
            {"limit": limit, "offset": offset},
        )
        return self.client.get(endpoint)

    def remove_measurement(
        self,
        study_id: str,
        measurement_id: str,
        project_id: str | None = None,
    ) -> None:
        """Remove a measurement from a study.

        Parameters
        ----------
        study_id : str
            The ID of the study.
        measurement_id : str
            The ID of the measurement to remove.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = (
            f"{self._prefix(project_id)}/{study_id}/measurements/{measurement_id}"
        )
        self.client.delete(endpoint)
