"""Project client for managing projects within organizations.

This module provides the :class:`ProjectClient` for creating, reading,
updating, and deleting projects, which organize resources (studies,
simulations, pipelines, etc.) within an organization.
"""

from __future__ import annotations

from typing import Any

from .models import (
    InviteResult,
    PaginatedList,
    Project,
    ProjectRoleInfo,
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

    def roles(self) -> list[ProjectRoleInfo]:
        """List the project roles that can be granted.

        Returns
        -------
        list[ProjectRoleInfo]
            Each role's ``id``, ``name`` (``"Project Admin"``,
            ``"Project Contributor"``, ``"Project Viewer"``) and description.
        """
        return [ProjectRoleInfo(**row) for row in self.client.get("/roles/project")]

    def _role_id(self, role: str) -> str:
        """Resolve a project role name (case-insensitive) or id to its id."""
        roles = self.roles()
        for r in roles:
            if role in (r.id, r.name) or role.lower() == r.name.lower():
                return r.id
        names = ", ".join(repr(r.name) for r in roles)
        raise ValueError(f"Unknown project role {role!r}. Choose one of: {names}.")

    def add_member(
        self, project_id: str, user_id: str, role: str = "Project Contributor"
    ) -> None:
        """Grant an organization member a role in a project.

        Also changes the role of someone who is already a project member. The
        user must already belong to the organization; to invite someone new,
        use :meth:`invite_member`.

        Parameters
        ----------
        project_id : str
            The project to grant access to.
        user_id : str
            The organization member's user id.
        role : str, optional
            Project role name or id. Defaults to ``"Project Contributor"``.
        """
        self.client.post(
            f"{self._BASE}/{project_id}/members",
            {"user_id": user_id, "project_role_id": self._role_id(role)},
        )

    def invite_member(
        self, project_id: str, email: str, role: str = "Project Contributor"
    ) -> InviteResult:
        """Invite someone to a project by email, adding them to the org if needed.

        A person outside the organization joins it as a ``Member``; an existing
        member keeps their organization role. Unregistered emails are sent a
        sign-up invitation.

        Parameters
        ----------
        project_id : str
            The project to grant access to.
        email : str
            Email address of the person to invite.
        role : str, optional
            Project role name or id. Defaults to ``"Project Contributor"``.

        Returns
        -------
        InviteResult
            The user's id, and whether an invitation email was sent.

        Examples
        --------
        >>> client.project.invite_member(project_id, "new.user@example.com")
        """
        response = self.client.post(
            f"{self._BASE}/{project_id}/invites",
            {"email": email, "project_role_id": self._role_id(role)},
        )
        return InviteResult(**response)

    def update_member(self, project_id: str, user_id: str, role: str) -> None:
        """Change a project member's role.

        Parameters
        ----------
        project_id : str
            The project.
        user_id : str
            The member's user id.
        role : str
            New project role name or id.
        """
        self.client.patch(
            f"{self._BASE}/{project_id}/members/{user_id}",
            {"project_role_id": self._role_id(role)},
        )

    def remove_member(self, project_id: str, user_id: str) -> None:
        """Remove a user from a project (their organization membership is kept).

        Parameters
        ----------
        project_id : str
            The project.
        user_id : str
            The member's user id.
        """
        self.client.delete(f"{self._BASE}/{project_id}/members/{user_id}")
