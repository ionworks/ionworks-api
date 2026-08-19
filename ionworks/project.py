"""Project client for managing projects within organizations.

This module provides the :class:`ProjectClient` for creating, reading,
updating, and deleting projects, which organize resources (studies,
simulations, pipelines, etc.) within an organization.
"""

from __future__ import annotations

from typing import Any

from .models import (
    PaginatedList,
    Project,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)


class ProjectClient:
    """Client for managing projects within organizations.

    Projects group resources like studies, simulations, pipelines, and
    optimizations within an organization.
    """

    _BASE = "/projects"

    def __init__(self, client: Any) -> None:
        """Initialize the ProjectClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def get(self, project_id: str) -> Project:
        """Get a specific project by ID.

        Parameters
        ----------
        project_id : str
            The ID of the project to retrieve.

        Returns
        -------
        Project
            The requested project object.
        """
        endpoint = f"{self._BASE}/{project_id}"
        response_data = self.client.get(endpoint)
        return Project(**response_data)

    def list(
        self,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Project]:
        """List projects with optional filtering.

        Parameters
        ----------
        limit : int | None, optional
            Maximum number of projects to return per page.
        offset : int | None, optional
            Number of projects to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on project name.
        name_exact : str | None, optional
            Exact match on project name.
        created_after : str | None, optional
            ISO datetime; return projects created after this time.
        created_before : str | None, optional
            ISO datetime; return projects created before this time.
        updated_after : str | None, optional
            ISO datetime; return projects updated after this time.
        updated_before : str | None, optional
            ISO datetime; return projects updated before this time.
        order_by : str | None, optional
            Column to sort by.
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Project]
            A list of project objects.
        """
        filter_params = _build_filter_params(
            name=name,
            name_exact=name_exact,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            order_by=order_by,
            order=order,
        )
        endpoint = _build_endpoint(
            self._BASE,
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Project)

    def create(self, data: dict[str, Any] | None = None) -> Project:
        """Create a new project.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the project data. Required fields: ``name``.
            Optional fields: ``description``.

        Returns
        -------
        Project
            The newly created project object.
        """
        endpoint = self._BASE
        response_data = self.client.post(endpoint, data)
        return Project(**response_data)

    def update(
        self,
        project_id: str,
        data: dict[str, Any] | None = None,
    ) -> Project:
        """Update an existing project.

        Parameters
        ----------
        project_id : str
            The ID of the project to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports ``name`` and
            ``description``.

        Returns
        -------
        Project
            The updated project object.
        """
        endpoint = f"{self._BASE}/{project_id}"
        response_data = self.client.patch(endpoint, data)
        return Project(**response_data)

    def delete(self, project_id: str) -> None:
        """Delete a project by ID.

        Parameters
        ----------
        project_id : str
            The ID of the project to delete.
        """
        endpoint = f"{self._BASE}/{project_id}"
        self.client.delete(endpoint)
