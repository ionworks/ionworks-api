"""Cycler client for managing cyclers.

This module provides the :class:`CyclerClient` for creating, reading,
updating, and deleting cyclers, which represent battery test equipment that
belongs to a site and owns one or more channels.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .models import (
    Channel,
    Cycler,
    CyclerDetail,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
    create_or_get,
)


class CyclerClient:
    """Client for managing cyclers."""

    def __init__(self, client: Any) -> None:
        """Initialize the CyclerClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def get(self, cycler_id: str) -> Cycler:
        """Get a specific cycler by ID."""
        endpoint = f"/cyclers/{cycler_id}"
        response_data = self.client.get(endpoint)
        return Cycler(**response_data)

    def list(
        self,
        site_id: str,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        project_id: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        created_by_email: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Cycler]:
        """List cyclers for a site with optional filtering.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        site_id : str
            The ID of the site.
        limit : int | None, optional
            Maximum number of cyclers to return per page.
        offset : int | None, optional
            Number of cyclers to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on cycler name.
        name_exact : str | None, optional
            Exact match on cycler name. Takes precedence over ``name``.
        project_id : str | None, optional
            Return only cyclers owned by this project (exact match).
        manufacturer : str | None, optional
            Case-insensitive substring match on the cycler manufacturer.
        model : str | None, optional
            Case-insensitive substring match on the cycler model.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after : str | None, optional
            ISO datetime; return cyclers created after this time.
        created_before : str | None, optional
            ISO datetime; return cyclers created before this time.
        updated_after : str | None, optional
            ISO datetime; return cyclers updated after this time.
        updated_before : str | None, optional
            ISO datetime; return cyclers updated before this time.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Cycler]
            All cyclers belonging to the site.
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
        if project_id is not None:
            filter_params["project_id"] = project_id
        if manufacturer is not None:
            filter_params["manufacturer"] = f"ilike.%{manufacturer}%"
        if model is not None:
            filter_params["model"] = f"ilike.%{model}%"
        endpoint = _build_endpoint(
            f"/sites/{site_id}/cyclers",
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Cycler)

    def detail(self, cycler_id: str) -> CyclerDetail:
        """Get full details for a cycler, including its channels.

        Fetches the cycler and its channel list in parallel.

        Parameters
        ----------
        cycler_id : str
            The cycler ID.

        Returns
        -------
        CyclerDetail
            Cycler metadata, its site foreign key, and the list of channels.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            cycler_future = pool.submit(
                self.client.get,
                f"/cyclers/{cycler_id}",
            )
            channels_future = pool.submit(self._list_all_channels, cycler_id)

        cycler = Cycler(**cycler_future.result())
        return CyclerDetail(
            cycler=cycler,
            site_id=cycler.site_id,
            channels=channels_future.result(),
        )

    def detail_from_cycler(self, cycler: Cycler) -> CyclerDetail:
        """Build a :class:`CyclerDetail` from an already-fetched cycler.

        Fetches only the channel list — the caller supplies the cycler, avoiding
        a redundant ``GET /cyclers/{id}``. Used by ``SiteClient.detail`` where
        the cyclers are already in hand from the site's cycler list.

        Parameters
        ----------
        cycler : Cycler
            A fully-populated cycler (e.g. from a list response).

        Returns
        -------
        CyclerDetail
            Cycler metadata, its site foreign key, and the list of channels.
        """
        return CyclerDetail(
            cycler=cycler,
            site_id=cycler.site_id,
            channels=self._list_all_channels(cycler.id),
        )

    def _list_all_channels(self, cycler_id: str) -> list[Channel]:
        """Fetch every channel under a cycler, paging past the backend default.

        ``detail()`` must not silently drop channels beyond the first page, so
        this walks pages until the accumulated count reaches ``total``.
        """
        page_size = 100
        offset = 0
        channels: list[Channel] = []
        while True:
            page = self.client.channel.list(cycler_id, limit=page_size, offset=offset)
            channels.extend(page)
            offset += len(page)
            if len(page) == 0 or offset >= page.total:
                break
        return channels

    def update(
        self,
        cycler_id: str,
        data: dict[str, Any],
    ) -> Cycler:
        """Update an existing cycler.

        Parameters
        ----------
        cycler_id : str
            The ID of the cycler to update.
        data : dict[str, Any]
            Dictionary containing the fields to update.

        Returns
        -------
        Cycler
            The updated cycler.
        """
        endpoint = f"/cyclers/{cycler_id}"
        response_data = self.client.patch(endpoint, data)
        return Cycler(**response_data)

    def delete(self, cycler_id: str) -> None:
        """Delete a cycler by ID.

        Deleting a cycler cascades to its channels.
        """
        endpoint = f"/cyclers/{cycler_id}"
        self.client.delete(endpoint)

    def create(
        self,
        site_id: str,
        data: dict[str, Any],
    ) -> Cycler:
        """Create a new cycler under a site.

        Parameters
        ----------
        site_id : str
            The ID of the parent site.
        data : dict[str, Any]
            Dictionary containing the cycler data. Must include
            ``project_id`` — the project that will own the cycler.

        Returns
        -------
        Cycler
            The newly created cycler.
        """
        endpoint = f"/sites/{site_id}/cyclers"
        response_data = self.client.post(endpoint, data)
        return Cycler(**response_data)

    def create_or_get(
        self,
        site_id: str,
        data: dict[str, Any],
    ) -> Cycler:
        """Create a new cycler or get the existing one on name conflict.

        Parameters
        ----------
        site_id : str
            The ID of the parent site.
        data : dict[str, Any]
            Dictionary containing the cycler data.

        Returns
        -------
        Cycler
            The cycler (newly created or existing).
        """
        name = data.get("name")

        def _find_by_name(cycler_name: str) -> Cycler | None:
            for cycler in self.list(site_id, name_exact=cycler_name):
                if cycler.name == cycler_name:
                    return cycler
            return None

        return create_or_get(
            create=lambda: self.create(site_id, data),
            get_by_id=self.get,
            find_by_name=_find_by_name,
            name=name,
            resource_label="Cycler",
        )
