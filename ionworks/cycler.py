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
    CyclerServiceEvent,
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
        version: str | None = None,
        serial_number: str | None = None,
        firmware_version: str | None = None,
        hostname: str | None = None,
        created_by_email: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        calibration_due_after: str | None = None,
        calibration_due_before: str | None = None,
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
            Case-insensitive substring match on the cycler model (type).
        version : str | None, optional
            Case-insensitive substring match on the hardware/product version.
        serial_number : str | None, optional
            Case-insensitive substring match on the serial number.
        firmware_version : str | None, optional
            Case-insensitive substring match on the firmware revision.
        hostname : str | None, optional
            Case-insensitive substring match on the control-host DNS name.
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
        calibration_due_after : str | None, optional
            ISO datetime; return cyclers whose calibration falls due after this
            time. Cyclers with no calibration schedule are always excluded —
            they have no due date to compare.
        calibration_due_before : str | None, optional
            ISO datetime; return cyclers whose calibration falls due before this
            time. Pass "now" in ISO form to find overdue equipment.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``,
            ``"calibration_due_at"``, ``"last_calibrated_at"``, ...).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Cycler]
            All cyclers belonging to the site.

        Examples
        --------
        Find equipment that is overdue for calibration, soonest first::

            client.cycler.list(
                site_id,
                calibration_due_before="2026-08-03T00:00:00Z",
                order_by="calibration_due_at",
                order="asc",
            )
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
        for field, value in (
            ("manufacturer", manufacturer),
            ("model", model),
            ("version", version),
            ("serial_number", serial_number),
            ("firmware_version", firmware_version),
            ("hostname", hostname),
        ):
            if value is not None:
                filter_params[field] = f"ilike.%{value}%"
        # The backend exposes these as *_gt / *_lt range params, matching the
        # created_at / updated_at contract that _build_filter_params already
        # covers for the fields it knows about.
        if calibration_due_after is not None:
            filter_params["calibration_due_at_gt"] = calibration_due_after
        if calibration_due_before is not None:
            filter_params["calibration_due_at_lt"] = calibration_due_before
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

    # --- Service events (instrument-level downtime) --- #

    def list_service_events(
        self,
        cycler_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> PaginatedList[CyclerServiceEvent]:
        """List a cycler's service history, newest first.

        Parameters
        ----------
        cycler_id : str
            The cycler whose history to read.
        limit : int | None, optional
            Page size.
        offset : int | None, optional
            Rows to skip.

        Returns
        -------
        PaginatedList[CyclerServiceEvent]
            Service events. The one with ``performed_at is None``, if any, is
            the cycler's current service.
        """
        endpoint = _build_endpoint(
            f"/cyclers/{cycler_id}/service_events",
            {"limit": limit, "offset": offset},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, CyclerServiceEvent)

    def start_service(
        self,
        cycler_id: str,
        event_type: str,
        scheduled_for: str,
        *,
        scheduled_until: str | None = None,
        notes: str | None = None,
    ) -> CyclerServiceEvent:
        """Take a cycler out of service for planned work.

        Opens one event and an incident on **every** channel of the cycler, so
        the whole instrument reads as down. This is the supported way to
        service a cycler: taking channels out one at a time cannot be completed
        as a single action, and leaves no record of what the work was.

        Channels already out of service keep their own incident (a broken relay
        is more specific than "being serviced") and stay out when the service
        completes.

        Parameters
        ----------
        cycler_id : str
            The cycler going out of service.
        event_type : str
            One of ``"calibration"``, ``"preventive_maintenance"``,
            ``"firmware"``, ``"repair"``, ``"other"``.
        scheduled_for : str
            ISO datetime for the booked visit. Required: a cycler must not be
            parked out of service with no date attached.
        scheduled_until : str | None, optional
            ISO datetime for when the visit is expected to end, making the
            booking a span rather than a start instant. Must be after
            ``scheduled_for``. Optional, but supplying it is what lets the lab
            say when the cycler comes back and lets planned work booked on
            these channels be checked against the outage. Keyword-only.
        notes : str | None, optional
            Free-text detail, copied to each channel incident. Keyword-only, so
            that adding ``scheduled_until`` ahead of it cannot rebind a value
            an existing caller passed positionally.

        Returns
        -------
        CyclerServiceEvent
            The newly opened event.

        Raises
        ------
        IonworksError
            With ``error_code == "CONFLICT"`` (HTTP 409) if the cycler already
            has an open service event, or if any channel still has a running
            measurement — finish or stop those tests first. With
            ``error_code == "BAD_REQUEST"`` (HTTP 400) if ``scheduled_until``
            is not after ``scheduled_for``.

        Examples
        --------
        Take a cycler out for a calibration booked over two days::

            event = client.cycler.start_service(
                cycler_id,
                event_type="calibration",
                scheduled_for="2026-09-15T09:00:00Z",
                scheduled_until="2026-09-17T17:00:00Z",
                notes="Annual calibration",
            )
        """
        endpoint = f"/cyclers/{cycler_id}/service_events"
        payload: dict[str, Any] = {
            "event_type": event_type,
            "scheduled_for": scheduled_for,
        }
        if scheduled_until is not None:
            payload["scheduled_until"] = scheduled_until
        if notes is not None:
            payload["notes"] = notes
        response_data = self.client.post(endpoint, payload)
        return CyclerServiceEvent(**response_data)

    def complete_service(
        self,
        cycler_id: str,
        performed_at: str | None = None,
        notes: str | None = None,
        calibration_interval_days: int | None = None,
        event_type: str = "calibration",
    ) -> CyclerServiceEvent:
        """Record service as done on a cycler.

        Two paths, chosen by whether the cycler is currently out of service:

        - **It has an open event** (you called ``start_service`` first): that
          event is completed and every channel incident it opened is closed in
          one write, so the cycler cannot end up half returned to service.
          Channels that were out for their own faults stay out.
        - **It has no open event**: the common case of "I calibrated this, log
          it". A single already-completed event is recorded from ``event_type``.
          Nothing was taken out of service, so nothing needs returning — you do
          not have to open an event purely so it can be closed.

        For a ``calibration`` event this also advances the cycler's
        ``last_calibrated_at`` — and therefore ``calibration_due_at`` — which is
        the only supported way that clock moves.

        Parameters
        ----------
        cycler_id : str
            The cycler whose open service to complete.
        performed_at : str | None, optional
            ISO datetime the work was done. Defaults to now. Accepts a past
            time so work finished yesterday can be recorded today.
        notes : str | None, optional
            What was done.
        calibration_interval_days : int | None, optional
            Optionally update the calibration cadence at the same time.
            Ignored for non-calibration events.
        event_type : str, optional
            What the work was. Used only when the cycler has no open event and
            this call is recording it after the fact; ignored otherwise,
            because an open event already carries its own type. Defaults to
            ``"calibration"``.

        Returns
        -------
        CyclerServiceEvent
            The completed event.

        Examples
        --------
        Record the calibration and reset the cadence to a year::

            client.cycler.complete_service(
                cycler_id,
                notes="Calibrated and verified",
                calibration_interval_days=365,
            )
        """
        endpoint = f"/cyclers/{cycler_id}/service_events/complete"
        payload: dict[str, Any] = {"event_type": event_type}
        if performed_at is not None:
            payload["performed_at"] = performed_at
        if notes is not None:
            payload["notes"] = notes
        if calibration_interval_days is not None:
            payload["calibration_interval_days"] = calibration_interval_days
        response_data = self.client.post(endpoint, payload)
        return CyclerServiceEvent(**response_data)

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
