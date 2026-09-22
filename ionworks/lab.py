"""Lab client: point-in-time equipment occupancy for a project.

Wraps ``GET /projects/{project_id}/lab/status`` — the single aggregation the
lab wall renders from. :meth:`LabClient.status` is the only network call;
:meth:`utilization`, :meth:`free_channels`, :meth:`stale_channels`, and
:meth:`on_channel` are pure client-side views over that payload.

Each of those methods fetches its own snapshot by default, so a single call is
always internally consistent. Two *separate* calls fetch two snapshots and can
disagree if equipment changes in between; when you need several views of the
*same* instant (e.g. utilization alongside the free-channel list), fetch one
snapshot and pass it in via the ``status`` argument::

    s = client.lab.status(project_id)
    u = client.lab.utilization(project_id, status=s)
    free = client.lab.free_channels(project_id, status=s)  # same instant as u

State is *derived* (no live telemetry): a channel is ``occupied`` when a linked
measurement has no ``end_time`` and updated recently, ``stale`` when such a
measurement has gone quiet (likely stopped / forgotten), ``free`` when nothing
is running, and ``out_of_commission`` when taken out of service. The model is a
snapshot: it has no finish-time/ETA and no historical trend.
"""

from __future__ import annotations

from typing import Any

from .models import (
    ChannelState,
    FlatChannel,
    LabMeasurementSummary,
    LabStatus,
    Utilization,
)


class LabClient:
    """Read-only client for a project's lab-view occupancy."""

    def __init__(self, client: Any) -> None:
        """Initialize the LabClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def status(self, project_id: str) -> LabStatus:
        """Get the project's sites -> cyclers -> channels occupancy tree.

        Parameters
        ----------
        project_id : str
            The project whose equipment occupancy to fetch.

        Returns
        -------
        LabStatus
            The full tree with each channel's derived state and the on-test
            measurement summary, plus project-level occupancy counts.
        """
        response_data = self.client.get(f"/projects/{project_id}/lab/status")
        return LabStatus(**response_data)

    def utilization(
        self, project_id: str, *, status: LabStatus | None = None
    ) -> Utilization:
        """Compute the project's lab utilization (matches the lab wall).

        ``percent`` is the busy (``occupied`` + ``stale``) share of all
        channels, out-of-commission included in the denominator, rounded to a
        whole number. ``0`` when the project has no channels.

        Parameters
        ----------
        project_id : str
            The project whose utilization to compute.
        status : LabStatus | None, optional
            A pre-fetched snapshot to compute from. Pass one shared across
            several calls to keep their answers on the same instant; when
            omitted, a fresh snapshot is fetched.

        Returns
        -------
        Utilization
            The headline percent plus the raw occupancy counts.
        """
        s = self._resolve(project_id, status)
        total = s.occupied + s.stale + s.free + s.out_of_commission
        percent = round((s.occupied + s.stale) / total * 100) if total else 0
        return Utilization(
            percent=percent,
            occupied=s.occupied,
            stale=s.stale,
            free=s.free,
            out_of_commission=s.out_of_commission,
            total=total,
        )

    def _resolve(self, project_id: str, status: LabStatus | None) -> LabStatus:
        """Return the caller's snapshot, or fetch a fresh one if none given."""
        return status if status is not None else self.status(project_id)

    def _flatten(
        self, project_id: str, status: LabStatus | None = None
    ) -> list[FlatChannel]:
        """Flatten the tree to channels carrying their cycler + site context."""
        flat: list[FlatChannel] = []
        for site in self._resolve(project_id, status).sites:
            for cycler in site.cyclers:
                for channel in cycler.channels:
                    flat.append(
                        FlatChannel(
                            channel=channel,
                            cycler_id=cycler.id,
                            cycler_name=cycler.name,
                            site_id=site.id,
                            site_name=site.name,
                        )
                    )
        return flat

    def free_channels(
        self,
        project_id: str,
        *,
        min_amps: float | None = None,
        min_volts: float | None = None,
        max_volts: float | None = None,
        status: LabStatus | None = None,
    ) -> list[FlatChannel]:
        """List free channels, optionally filtered by required ratings.

        Answers "where can I run this cell". Each result carries its parent
        cycler and site names.

        Parameters
        ----------
        project_id : str
            The project whose channels to inspect.
        min_amps : float | None, optional
            Keep channels whose ``max_amps`` is at least this. Channels with no
            ``max_amps`` rating are excluded (an unrated channel can't be shown
            to satisfy the requirement).
        min_volts : float | None, optional
            Lower bound of the voltage window the cell needs. A channel
            qualifies only if its rated ``min_volts`` is at or below this; a
            channel missing the rating is excluded.
        max_volts : float | None, optional
            Upper bound of the voltage window the cell needs. A channel
            qualifies only if its rated ``max_volts`` is at or above this; a
            channel missing the rating is excluded.
        status : LabStatus | None, optional
            A pre-fetched snapshot to read from. Pass one shared across several
            calls to keep their answers on the same instant; when omitted, a
            fresh snapshot is fetched.

        Returns
        -------
        list[FlatChannel]
            Free channels matching all supplied constraints.
        """
        result: list[FlatChannel] = []
        for fc in self._flatten(project_id, status):
            ch = fc.channel
            if ch.state is not ChannelState.free:
                continue
            if min_amps is not None and (ch.max_amps is None or ch.max_amps < min_amps):
                continue
            if min_volts is not None and (
                ch.min_volts is None or ch.min_volts > min_volts
            ):
                continue
            if max_volts is not None and (
                ch.max_volts is None or ch.max_volts < max_volts
            ):
                continue
            result.append(fc)
        return result

    def stale_channels(
        self, project_id: str, *, status: LabStatus | None = None
    ) -> list[FlatChannel]:
        """List channels whose linked test has gone stale (needs attention).

        A ``stale`` channel has an un-finished measurement that stopped
        updating within the staleness window — likely a stopped, silently
        failed, or forgotten test tying up equipment. Each result carries its
        cycler + site names and the offending measurement summary.

        Parameters
        ----------
        project_id : str
            The project whose channels to inspect.
        status : LabStatus | None, optional
            A pre-fetched snapshot to read from. Pass one shared across several
            calls to keep their answers on the same instant; when omitted, a
            fresh snapshot is fetched.

        Returns
        -------
        list[FlatChannel]
            Channels in the ``stale`` state.
        """
        return [
            fc
            for fc in self._flatten(project_id, status)
            if fc.channel.state is ChannelState.stale
        ]

    def on_channel(
        self,
        project_id: str,
        channel_id: str,
        *,
        status: LabStatus | None = None,
    ) -> LabMeasurementSummary | None:
        """Return the measurement on test on a channel, or None.

        Parameters
        ----------
        project_id : str
            The project the channel belongs to.
        channel_id : str
            The channel to inspect.
        status : LabStatus | None, optional
            A pre-fetched snapshot to read from. Pass one shared across several
            calls to keep their answers on the same instant; when omitted, a
            fresh snapshot is fetched.

        Returns
        -------
        LabMeasurementSummary | None
            The on-test measurement summary, or None when the channel is free,
            out of commission, or not found in the project.
        """
        for site in self._resolve(project_id, status).sites:
            for cycler in site.cyclers:
                for channel in cycler.channels:
                    if channel.id == channel_id:
                        return channel.measurement
        return None

    def list_watched(self, project_id: str) -> list[str]:
        """List the measurement IDs the current user is watching.

        A network call to ``GET /projects/{project_id}/lab/watched``. Returns a
        flat list of measurement IDs; may include watches on measurements that
        have since finished (the "my channels" view intersects them with the
        live status, so finished watches simply don't surface as channels). For
        the channel-level view, use :meth:`watched_channels`.

        Parameters
        ----------
        project_id : str
            The project whose watches to list.

        Returns
        -------
        list[str]
            IDs of measurements the current user is watching.
        """
        response_data = self.client.get(f"/projects/{project_id}/lab/watched")
        return list(response_data.get("measurement_ids", []))

    def watched_channels(
        self, project_id: str, *, status: LabStatus | None = None
    ) -> list[FlatChannel]:
        """List the channels the current user is watching ("my channels").

        A watch is keyed on a channel's live measurement, so this returns the
        channels whose on-test measurement the user is watching -- exactly the
        lab wall's "my channels" filter. A channel drops out on its own once its
        test finishes (the watch no longer matches a live measurement), so this
        never returns a free channel. Reads the ``watched`` flag already present
        on each channel's measurement in the status snapshot, so it needs no
        extra network call beyond fetching (or reusing) the status.

        Parameters
        ----------
        project_id : str
            The project whose channels to inspect.
        status : LabStatus | None, optional
            A pre-fetched snapshot to read from. Pass one shared across several
            calls to keep their answers on the same instant; when omitted, a
            fresh snapshot is fetched.

        Returns
        -------
        list[FlatChannel]
            Channels whose live measurement the current user is watching, each
            carrying its cycler + site names.
        """
        return [
            fc
            for fc in self._flatten(project_id, status)
            if fc.channel.measurement is not None and fc.channel.measurement.watched
        ]
