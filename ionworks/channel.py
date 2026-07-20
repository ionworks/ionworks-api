"""Channel client for managing cycler channels.

This module provides the :class:`ChannelClient` for creating, reading,
updating, and deleting channels, which represent individual test channels
belonging to a cycler.
"""

from __future__ import annotations

from typing import Any

from .models import (
    Channel,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
    create_or_get,
)


class ChannelClient:
    """Client for managing channels."""

    def __init__(self, client: Any) -> None:
        """Initialize the ChannelClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def get(self, channel_id: str) -> Channel:
        """Get a specific channel by ID."""
        endpoint = f"/channels/{channel_id}"
        response_data = self.client.get(endpoint)
        return Channel(**response_data)

    def list(
        self,
        cycler_id: str,
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
    ) -> PaginatedList[Channel]:
        """List channels for a cycler with optional filtering.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        cycler_id : str
            The ID of the cycler.
        limit : int | None, optional
            Maximum number of channels to return per page.
        offset : int | None, optional
            Number of channels to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on channel name.
        name_exact : str | None, optional
            Exact match on channel name. Takes precedence over ``name``.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after : str | None, optional
            ISO datetime; return channels created after this time.
        created_before : str | None, optional
            ISO datetime; return channels created before this time.
        updated_after : str | None, optional
            ISO datetime; return channels updated after this time.
        updated_before : str | None, optional
            ISO datetime; return channels updated before this time.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Channel]
            All channels belonging to the cycler.
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
            f"/cyclers/{cycler_id}/channels",
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Channel)

    def update(
        self,
        channel_id: str,
        data: dict[str, Any],
    ) -> Channel:
        """Update an existing channel.

        Parameters
        ----------
        channel_id : str
            The ID of the channel to update.
        data : dict[str, Any]
            Dictionary containing the fields to update.

        Returns
        -------
        Channel
            The updated channel.

        Raises
        ------
        IonworksError
            With ``error_code == "CONFLICT"`` (HTTP 409) if
            ``out_of_commission`` is set to True while the channel has an
            active (un-finished) time_series measurement — finish that
            measurement first.
        """
        endpoint = f"/channels/{channel_id}"
        response_data = self.client.patch(endpoint, data)
        return Channel(**response_data)

    def delete(self, channel_id: str) -> None:
        """Delete a channel by ID."""
        endpoint = f"/channels/{channel_id}"
        self.client.delete(endpoint)

    def create(
        self,
        cycler_id: str,
        data: dict[str, Any],
    ) -> Channel:
        """Create a new channel under a cycler.

        Parameters
        ----------
        cycler_id : str
            The ID of the parent cycler.
        data : dict[str, Any]
            Channel fields. ``name`` is required. Optional: ``notes`` (str),
            ``out_of_commission`` (bool, default False, marks a channel out of
            service), and the electrical ratings ``max_amps`` (float, rated max
            current) and the voltage window ``min_volts`` / ``max_volts``
            (float), used to filter channels in the Lab view.

        Returns
        -------
        Channel
            The newly created channel.
        """
        endpoint = f"/cyclers/{cycler_id}/channels"
        response_data = self.client.post(endpoint, data)
        return Channel(**response_data)

    def create_or_get(
        self,
        cycler_id: str,
        data: dict[str, Any],
    ) -> Channel:
        """Create a new channel or get the existing one on name conflict.

        Parameters
        ----------
        cycler_id : str
            The ID of the parent cycler.
        data : dict[str, Any]
            Dictionary containing the channel data.

        Returns
        -------
        Channel
            The channel (newly created or existing).
        """
        name = data.get("name")

        def _find_by_name(channel_name: str) -> Channel | None:
            for channel in self.list(cycler_id, name_exact=channel_name):
                if channel.name == channel_name:
                    return channel
            return None

        return create_or_get(
            create=lambda: self.create(cycler_id, data),
            get_by_id=self.get,
            find_by_name=_find_by_name,
            name=name,
            resource_label="Channel",
        )
