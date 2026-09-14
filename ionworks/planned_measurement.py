"""Planned measurement client for scheduling future lab tests.

This module provides the :class:`PlannedMeasurementClient` for creating,
reading, updating, and deleting planned measurements -- future, project-scoped
test requests that can reserve a channel over a time window once scheduled.
"""

from __future__ import annotations

from typing import Any

from ._project_id import resolve_project_id
from .models import (
    AutoScheduleAssignment,
    AutoScheduleProposal,
    PaginatedList,
    PlannedMeasurement,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
    create_or_get,
)


class PlannedMeasurementClient:
    """Client for managing planned measurements within projects.

    A planned measurement is a future test request. Every plan names a
    ``protocol_id`` and a ``cell_specification_id``. A requester creates it as
    ``requested`` (with an ``estimated_duration_seconds`` and no channel); a
    scheduler later assigns a ``channel_id`` and a
    ``[planned_start_time, planned_end_time)`` window, moving it to
    ``scheduled``. It never creates or mutates a real ``cell_measurement`` --
    the real run links back to the plan when it starts.

    All methods accept ``project_id`` as an optional argument. When omitted,
    the value falls back to the ``project_id`` configured on the parent
    :class:`~ionworks.Ionworks` client (resolved from the
    ``IONWORKS_PROJECT_ID`` env var if not passed explicitly). Methods raise
    ``ValueError`` if no project_id is available from any source.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the PlannedMeasurementClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def _prefix(self, project_id: str) -> str:
        """Build the base URL prefix for planned measurement endpoints."""
        return f"/projects/{project_id}/planned_measurements"

    def get(
        self,
        planned_measurement_id: str,
        project_id: str | None = None,
    ) -> PlannedMeasurement:
        """Get a specific planned measurement by ID.

        Parameters
        ----------
        planned_measurement_id : str
            The ID of the planned measurement to retrieve.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        PlannedMeasurement
            The requested planned measurement.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{planned_measurement_id}"
        response_data = self.client.get(endpoint)
        return PlannedMeasurement(**response_data)

    def list(
        self,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        *,
        status: str | None = None,
        channel_id: str | None = None,
        name: str | None = None,
        name_exact: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[PlannedMeasurement]:
        """List planned measurements within a project.

        Parameters
        ----------
        project_id : str | None, optional
            The ID of the project to list planned measurements for. Defaults to
            the project_id set on the Ionworks client.
        limit : int | None, optional
            Maximum number of planned measurements to return per page.
        offset : int | None, optional
            Number of planned measurements to skip for pagination.
        status : str | None, optional
            Filter by lifecycle status (``"requested"``, ``"scheduled"``,
            ``"in_progress"``, ``"completed"``, ``"cancelled"``).
        channel_id : str | None, optional
            Filter to planned measurements reserved on a specific channel.
        name : str | None, optional
            Case-insensitive substring match on name.
        name_exact : str | None, optional
            Exact match on name. Takes precedence over ``name``.
        order_by : str | None, optional
            Column to sort by (e.g. ``"planned_start_time"``, ``"created_at"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[PlannedMeasurement]
            The matching planned measurements for the project.
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
            {
                "limit": limit,
                "offset": offset,
                "status": status,
                "channel_id": channel_id,
                **filter_params,
            },
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, PlannedMeasurement)

    def create(
        self,
        data: dict[str, Any],
        project_id: str | None = None,
    ) -> PlannedMeasurement:
        """Create a planned measurement (a future test request).

        Parameters
        ----------
        data : dict[str, Any]
            Planned measurement fields. Every planned measurement must name a
            protocol and a cell specification, so ``name``, ``protocol_id`` (a
            named ``experiment_template``), and ``cell_specification_id`` (the
            spec the requester wants tested) are all required. ``status``
            defaults to ``"requested"``; a ``requested`` row additionally
            requires ``estimated_duration_seconds`` while a ``scheduled`` row
            requires ``channel_id``, ``planned_start_time``, and
            ``planned_end_time``. Optional: ``cell_instance_id`` (the concrete
            cell, usually set by the scheduler), ``test_setup``,
            ``setup_duration_seconds``, ``teardown_duration_seconds``,
            ``notes``.
        project_id : str | None, optional
            The ID of the project to create it in. Defaults to the project_id
            set on the Ionworks client.

        Returns
        -------
        PlannedMeasurement
            The newly created planned measurement.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = self._prefix(project_id)
        response_data = self.client.post(endpoint, data)
        return PlannedMeasurement(**response_data)

    def create_or_get(
        self,
        data: dict[str, Any],
        project_id: str | None = None,
    ) -> PlannedMeasurement:
        """Create a planned measurement or get the existing one on name conflict.

        Parameters
        ----------
        data : dict[str, Any]
            The planned measurement data (see :meth:`create`).
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        PlannedMeasurement
            The planned measurement (newly created or existing).
        """
        project_id = resolve_project_id(self.client, project_id)
        name = data.get("name")

        def _find_by_name(measurement_name: str) -> PlannedMeasurement | None:
            for planned in self.list(project_id, name_exact=measurement_name):
                if planned.name == measurement_name:
                    return planned
            return None

        return create_or_get(
            create=lambda: self.create(data, project_id),
            get_by_id=lambda pid: self.get(pid, project_id),
            find_by_name=_find_by_name,
            name=name,
            resource_label="PlannedMeasurement",
        )

    def update(
        self,
        planned_measurement_id: str,
        data: dict[str, Any],
        project_id: str | None = None,
    ) -> PlannedMeasurement:
        """Update a planned measurement (partial).

        Parameters
        ----------
        planned_measurement_id : str
            The ID of the planned measurement to update.
        data : dict[str, Any]
            Fields to update. To schedule a requested measurement, prefer the
            :meth:`schedule` convenience method.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        PlannedMeasurement
            The updated planned measurement.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{planned_measurement_id}"
        response_data = self.client.patch(endpoint, data)
        return PlannedMeasurement(**response_data)

    def schedule(
        self,
        planned_measurement_id: str,
        channel_id: str,
        planned_start_time: str,
        planned_end_time: str,
        project_id: str | None = None,
    ) -> PlannedMeasurement:
        """Schedule a requested measurement onto a channel and time window.

        Convenience wrapper over :meth:`update` that moves a ``requested``
        planned measurement to ``scheduled`` by assigning the channel and the
        ``[planned_start_time, planned_end_time)`` reservation.

        Parameters
        ----------
        planned_measurement_id : str
            The ID of the planned measurement to schedule.
        channel_id : str
            The channel to reserve for this measurement.
        planned_start_time : str
            ISO datetime the reservation starts.
        planned_end_time : str
            ISO datetime the reservation ends (must be after the start).
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        PlannedMeasurement
            The scheduled planned measurement.
        """
        return self.update(
            planned_measurement_id,
            {
                "status": "scheduled",
                "channel_id": channel_id,
                "planned_start_time": planned_start_time,
                "planned_end_time": planned_end_time,
            },
            project_id,
        )

    def auto_schedule_proposal(
        self,
        planned_measurement_ids: list[str],
        start_after: str | None = None,
        project_id: str | None = None,
    ) -> AutoScheduleProposal:
        """Propose earliest channel reservations for selected test requests.

        The order of ``planned_measurement_ids`` sets scheduling priority. The
        search is unbounded in time, so each request gets the earliest start
        that actually fits, however far out that is. Review the transient
        result before calling :meth:`apply_auto_schedule_proposal`; an
        assignment is only unscheduled when the project has no channel that can
        ever take it.

        Parameters
        ----------
        planned_measurement_ids : list[str]
            Requested planned measurements to schedule, in priority order.
        start_after : str | None, optional
            ISO datetime the batch may not start before, for when an operator
            cannot begin immediately. Defaults to the current time, which
            reserves channel time starting right away. Values in the past are
            clamped to the current time.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        AutoScheduleProposal
            Proposed reservations and any unscheduled reasons.
        """
        project_id = resolve_project_id(self.client, project_id)
        payload: dict[str, Any] = {"planned_measurement_ids": planned_measurement_ids}
        if start_after is not None:
            payload["start_after"] = start_after
        response_data = self.client.post(
            f"{self._prefix(project_id)}/auto_schedule_proposal", payload
        )
        return AutoScheduleProposal(**response_data)

    def apply_auto_schedule_proposal(
        self,
        assignments: list[AutoScheduleAssignment | dict[str, Any]],
        project_id: str | None = None,
    ) -> list[PlannedMeasurement]:
        """Apply complete assignments from a reviewed auto-schedule proposal.

        The API verifies each assignment against its proposal-time version and
        current channel availability. On a stale or conflicting proposal it
        rolls back the batch and raises ``IonworksError`` with
        ``error_code == "SCHEDULE_STALE"``; generate a new proposal before
        retrying.

        Parameters
        ----------
        assignments : list[AutoScheduleAssignment | dict[str, Any]]
            Complete assignments selected from a reviewed proposal. Exclude
            assignments whose ``is_scheduled`` is false.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        list[PlannedMeasurement]
            The newly scheduled planned measurements.
        """
        project_id = resolve_project_id(self.client, project_id)
        payload = {
            "assignments": [
                assignment.model_dump(mode="json")
                if isinstance(assignment, AutoScheduleAssignment)
                else assignment
                for assignment in assignments
            ]
        }
        response_data = self.client.post(
            f"{self._prefix(project_id)}/apply_auto_schedule_proposal", payload
        )
        return [PlannedMeasurement(**item) for item in response_data]

    def cancel(
        self,
        planned_measurement_id: str,
        project_id: str | None = None,
    ) -> PlannedMeasurement:
        """Cancel a planned measurement.

        Parameters
        ----------
        planned_measurement_id : str
            The ID of the planned measurement to cancel.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.

        Returns
        -------
        PlannedMeasurement
            The cancelled planned measurement.
        """
        return self.update(planned_measurement_id, {"status": "cancelled"}, project_id)

    def delete(
        self,
        planned_measurement_id: str,
        project_id: str | None = None,
    ) -> None:
        """Delete a planned measurement by ID.

        Parameters
        ----------
        planned_measurement_id : str
            The ID of the planned measurement to delete.
        project_id : str | None, optional
            The ID of the project. Defaults to the project_id set on the
            Ionworks client.
        """
        project_id = resolve_project_id(self.client, project_id)
        endpoint = f"{self._prefix(project_id)}/{planned_measurement_id}"
        self.client.delete(endpoint)
