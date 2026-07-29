"""Organization client for querying organization-level usage and limits.

This module provides the :class:`OrganizationClient` for reading an
organization's current usage and its configured usage limits. The
organization is resolved from the API key used to authenticate the client.
"""

from __future__ import annotations

from typing import Any

from .models import OrganizationUsage


class OrganizationClient:
    """Client for organization-level usage and limits.

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
