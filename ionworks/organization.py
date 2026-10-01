"""Organization client for usage, limits, and membership.

This module provides the :class:`OrganizationClient` for reading an
organization's current usage and limits and for managing its members. The
organization is resolved from the API key used to authenticate the client.
"""

from __future__ import annotations

from typing import Any, Literal

from .models import InviteResult, OrganizationMember, OrganizationUsage

OrgRole = Literal["Admin", "Member"]


class OrganizationClient:
    """Client for organization-level usage, limits, and membership.

    The organization is resolved from the API key, so these methods act on the
    organization that owns the key configured on the :class:`~ionworks.Ionworks`
    client.
    """

    _BASE = "/organizations"

    def __init__(self, client: Any) -> None:
        """Initialize the OrganizationClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client
        self._organization_id: str | None = None

    def usage(self) -> OrganizationUsage:
        """Get the organization's current usage and configured usage limits.

        Usage is aggregated across all members of the organization for the
        active calendar-month billing period. The organization is the one that
        owns the API key configured on the client.

        Returns
        -------
        OrganizationUsage
            Usage for the current calendar-month period (resets on the 1st).
            ``simulation`` has ``usage`` and ``limit``; ``compute`` also has a
            per-job-type breakdown in ``usage_by_type``. All values are in
            hours; a ``None`` limit means that type is unconstrained.

        Examples
        --------
        >>> usage = client.organization.usage()
        >>> sim_hours = usage.simulation.usage
        >>> compute_hours = usage.compute.usage
        >>> per_job = usage.compute.usage_by_type  # {"simulation": ..., ...}
        >>> resets_on = usage.period_end
        """
        endpoint = f"{self._BASE}/current/usage"
        response_data = self.client.get(endpoint)
        return OrganizationUsage(**response_data)

    def _org_id(self) -> str:
        """Return the id of the organization the client acts on.

        Uses the client's explicit ``organization_id`` when set; otherwise the
        organization the API key is authorized for, looked up once and cached.
        """
        explicit = getattr(self.client, "organization_id", None)
        if explicit:
            return explicit
        if self._organization_id is None:
            authorized = self.client.whoami().get("authorized_organization") or {}
            org_id = authorized.get("id")
            if not org_id:
                raise ValueError(
                    "Could not determine the organization for this client. "
                    "Pass organization_id to Ionworks() or set "
                    "IONWORKS_ORGANIZATION_ID."
                )
            self._organization_id = org_id
        return self._organization_id

    def _users_endpoint(self, user_id: str | None = None) -> str:
        base = f"{self._BASE}/{self._org_id()}/users"
        return f"{base}/{user_id}" if user_id else base

    def members(self) -> list[OrganizationMember]:
        """List the organization's members with their roles.

        Returns
        -------
        list[OrganizationMember]
            One entry per member: ``id``, ``email``, ``role`` (``"Admin"`` or
            ``"Member"``), and ``projects`` they hold a project role in.

        Examples
        --------
        >>> for m in client.organization.members():
        ...     print(m.email, m.role)
        """
        rows = self.client.get(self._users_endpoint())
        return [OrganizationMember.from_api(row) for row in rows]

    def get_member(self, email: str) -> OrganizationMember:
        """Find a member by email (case-insensitive).

        Parameters
        ----------
        email : str
            Email address of the member.

        Returns
        -------
        OrganizationMember
            The matching member.

        Raises
        ------
        ValueError
            If no member has that email.
        """
        wanted = email.strip().lower()
        for member in self.members():
            if (member.email or "").lower() == wanted:
                return member
        raise ValueError(f"No member with email {email!r} in this organization.")

    def invite(self, email: str, role: OrgRole = "Member") -> InviteResult:
        """Add someone to the organization by email.

        An existing member keeps their role (``already_member`` is True);
        change it with :meth:`set_role`. Needs permission to assign roles, and a
        deactivated member raises a 409 error.
        :meth:`~ionworks.project.ProjectClient.invite_member` also grants a
        project role.

        Parameters
        ----------
        email : str
            Email address to invite.
        role : {"Member", "Admin"}, optional
            Organization role for a new member. Defaults to ``"Member"``.

        Returns
        -------
        InviteResult
            The user's id, whether an invitation email was sent, whether they
            were already a member, and the organization role they now hold.

        Examples
        --------
        >>> result = client.organization.invite("new.user@example.com")
        >>> result.invite_sent
        True
        """
        response = self.client.post(
            f"{self._users_endpoint()}/by-email",
            {"email": email, "role_name": role},
        )
        return InviteResult(**response)

    def set_role(self, user_id: str, role: OrgRole) -> None:
        """Change a member's organization role.

        Parameters
        ----------
        user_id : str
            ID of the member (see :meth:`members` / :meth:`get_member`).
        role : {"Admin", "Member"}
            The new organization role.
        """
        self.client.patch(f"{self._users_endpoint(user_id)}/role", {"role_name": role})

    def deactivate(self, user_id: str) -> None:
        """Block a member's access while keeping their membership and roles.

        Parameters
        ----------
        user_id : str
            ID of the member to deactivate.
        """
        self.client.patch(self._users_endpoint(user_id), {"is_active": False})

    def reactivate(self, user_id: str) -> None:
        """Restore a deactivated member's access with their previous roles.

        Parameters
        ----------
        user_id : str
            ID of the member to reactivate.
        """
        self.client.patch(self._users_endpoint(user_id), {"is_active": True})

    def resend_invite(self, user_id: str) -> None:
        """Resend the sign-up invitation to a member who has not accepted it.

        Parameters
        ----------
        user_id : str
            ID of the invited member.
        """
        self.client.post(f"{self._users_endpoint(user_id)}/resend-invite", {})

    def remove(self, user_id: str) -> None:
        """Remove a member from the organization.

        Parameters
        ----------
        user_id : str
            ID of the member to remove.
        """
        self.client.delete(self._users_endpoint(user_id))
