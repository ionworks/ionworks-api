"""Site client for managing test sites.

This module provides the :class:`SiteClient` for creating, reading,
updating, and deleting sites, which represent physical locations that own
cyclers (and, in turn, channels).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .models import (
    Cycler,
    CyclerDetail,
    PaginatedList,
    Site,
    SiteDetail,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
    create_or_get,
)


class SiteClient:
    """Client for managing sites."""

    def __init__(self, client: Any) -> None:
        """Initialize the SiteClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def get(self, site_id: str) -> Site:
        """Get a specific site by ID."""
        endpoint = f"/sites/{site_id}"
        response_data = self.client.get(endpoint)
        return Site(**response_data)

    def list(
        self,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        location: str | None = None,
        created_by_email: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Site]:
        """List sites in the organization with optional filtering.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        limit : int | None, optional
            Maximum number of sites to return per page.
        offset : int | None, optional
            Number of sites to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on site name.
        name_exact : str | None, optional
            Exact match on site name. Takes precedence over ``name``.
        location : str | None, optional
            Case-insensitive substring match on the site location.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after : str | None, optional
            ISO datetime; return sites created after this time.
        created_before : str | None, optional
            ISO datetime; return sites created before this time.
        updated_after : str | None, optional
            ISO datetime; return sites updated after this time.
        updated_before : str | None, optional
            ISO datetime; return sites updated before this time.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Site]
            All sites belonging to the organization.
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
        if location is not None:
            filter_params["location"] = f"ilike.%{location}%"
        endpoint = _build_endpoint(
            "/sites",
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Site)

    def detail(self, site_id: str) -> SiteDetail:
        """Get full details for a site, including its cyclers and channels.

        Fetches the site and its cycler list in parallel, then expands each
        cycler to a :class:`~ionworks.models.CyclerDetail` (which includes its
        channels) in parallel.

        Parameters
        ----------
        site_id : str
            The site ID.

        Returns
        -------
        SiteDetail
            Site metadata and a list of cycler details.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            site_future = pool.submit(
                self.client.get,
                f"/sites/{site_id}",
            )
            cyclers_future = pool.submit(self._list_all_cyclers, site_id)

        site = Site(**site_future.result())
        cyclers_meta = cyclers_future.result()

        cycler_client = self.client.cycler
        cycler_details: list[CyclerDetail] = []
        if cyclers_meta:
            # cyclers_meta already holds full Cycler objects, so expand each by
            # fetching only its channels — no redundant per-cycler GET.
            with ThreadPoolExecutor(max_workers=min(len(cyclers_meta), 6)) as pool:
                detail_futures = [
                    pool.submit(cycler_client.detail_from_cycler, c)
                    for c in cyclers_meta
                ]
            cycler_details = [f.result() for f in detail_futures]

        return SiteDetail(site=site, cyclers=cycler_details)

    def _list_all_cyclers(self, site_id: str) -> list[Cycler]:
        """Fetch every cycler under a site, paging past the backend default.

        ``detail()`` must not silently drop cyclers beyond the first page, so
        this walks pages until the accumulated count reaches ``total``.
        """
        page_size = 100
        offset = 0
        cyclers: list[Cycler] = []
        while True:
            page = self.client.cycler.list(site_id, limit=page_size, offset=offset)
            cyclers.extend(page)
            offset += len(page)
            if len(page) == 0 or offset >= page.total:
                break
        return cyclers

    def update(
        self,
        site_id: str,
        data: dict[str, Any],
    ) -> Site:
        """Update an existing site.

        Parameters
        ----------
        site_id : str
            The ID of the site to update.
        data : dict[str, Any]
            Dictionary containing the fields to update.

        Returns
        -------
        Site
            The updated site.
        """
        endpoint = f"/sites/{site_id}"
        response_data = self.client.patch(endpoint, data)
        return Site(**response_data)

    def delete(self, site_id: str) -> None:
        """Delete a site by ID.

        Deleting a site cascades to its cyclers and their channels.
        """
        endpoint = f"/sites/{site_id}"
        self.client.delete(endpoint)

    def create(self, data: dict[str, Any]) -> Site:
        """Create a new site in the organization.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the site data.

        Returns
        -------
        Site
            The newly created site.
        """
        endpoint = "/sites"
        response_data = self.client.post(endpoint, data)
        return Site(**response_data)

    def create_or_get(self, data: dict[str, Any]) -> Site:
        """Create a new site or get the existing one on name conflict.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the site data.

        Returns
        -------
        Site
            The site (newly created or existing).
        """
        name = data.get("name")

        def _find_by_name(site_name: str) -> Site | None:
            for site in self.list(name_exact=site_name):
                if site.name == site_name:
                    return site
            return None

        return create_or_get(
            create=lambda: self.create(data),
            get_by_id=self.get,
            find_by_name=_find_by_name,
            name=name,
            resource_label="Site",
        )
