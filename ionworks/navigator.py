"""In-memory navigator for the spec → instance → measurement hierarchy.

The :class:`Navigator` walks the Ionworks platform's cell hierarchy and caches
results in memory for the lifetime of the instance. It is the canonical way to
write analysis scripts that iterate over many specs/instances/measurements:
each entity is fetched at most once per process, and listings are returned
sorted by name so iteration order is deterministic across runs.

This is an opt-in helper layered on top of :class:`Ionworks`. The underlying
sub-clients (``client.cell_spec``, ``client.cell_instance``,
``client.cell_measurement``) remain the primary API; reach for the navigator
when you want a single cached view of the hierarchy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Ionworks
    from .models import CellInstance, CellMeasurement, CellSpecification
    from .validators import DataFrame


class Navigator:
    """Cached navigator over the cell-spec hierarchy on the Ionworks platform.

    The navigator wraps an :class:`Ionworks` client and memoises every list /
    fetch call. Calling :meth:`instances` for the same spec twice returns the
    same list object without an extra API round-trip; the same applies to
    :meth:`measurements`, :meth:`steps`, and :meth:`time_series`.

    Listings are sorted by ``name`` so iteration order is stable across runs.

    Battery data is immutable once uploaded, so the only staleness mode is
    "new sibling appeared." For long-running notebooks, use :meth:`clear` to
    drop the whole cache or :meth:`invalidate` to drop a targeted subtree.

    Parameters
    ----------
    client : Ionworks, optional
        An existing client. If omitted, a default :class:`Ionworks` is
        constructed (which reads ``IONWORKS_API_KEY`` from the environment).
    page_size : int, optional
        Number of items per page when paginating ``cell_instance.list`` and
        ``cell_measurement.list``. Defaults to 200.

    Examples
    --------
    >>> from ionworks import Ionworks, Navigator
    >>> nav = Navigator(Ionworks())
    >>> for spec_name in nav.specs():
    ...     for inst in nav.instances(spec_name):
    ...         for m in nav.measurements(inst.id):
    ...             ts = nav.time_series(m.id)
    """

    def __init__(self, client: Ionworks | None = None, page_size: int = 200) -> None:
        if client is None:
            from .client import Ionworks

            client = Ionworks()
        self.client = client
        self.page_size = page_size
        self._specs: dict[str, CellSpecification] | None = None
        self._instances: dict[str, list[CellInstance]] = {}
        self._measurements: dict[str, list[CellMeasurement]] = {}
        self._steps: dict[str, DataFrame] = {}
        self._time_series: dict[str, DataFrame] = {}
        self._instance_spec: dict[str, str] = {}
        self._measurement_instance: dict[str, str] = {}

    def specs(self) -> dict[str, CellSpecification]:
        """Return all cell specifications in the organization, keyed by name.

        Returns
        -------
        dict[str, CellSpecification]
            Mapping of ``spec.name -> CellSpecification``. Cached after the
            first call.
        """
        if self._specs is None:
            self._specs = {s.name: s for s in self.client.cell_spec.list()}
        return self._specs

    def spec(self, name: str) -> CellSpecification:
        """Return the cell specification with the given name.

        Parameters
        ----------
        name : str
            Exact spec name.

        Returns
        -------
        CellSpecification
            The matching spec.

        Raises
        ------
        KeyError
            If no spec with the given name exists. The message lists the
            available spec names to make typos easy to spot.
        """
        specs = self.specs()
        if name not in specs:
            raise KeyError(
                f"No cell specification named {name!r}. Available: {sorted(specs)}"
            )
        return specs[name]

    def instances(self, spec_name: str) -> list[CellInstance]:
        """Return all cell instances for a spec, sorted by name.

        Parameters
        ----------
        spec_name : str
            Exact spec name.

        Returns
        -------
        list[CellInstance]
            All instances belonging to the spec, paginated under the hood and
            cached for subsequent calls.
        """
        if spec_name not in self._instances:
            spec = self.spec(spec_name)
            items = self._collect_all_pages(
                lambda limit, offset: self.client.cell_instance.list(
                    spec.id, limit=limit, offset=offset
                )
            )
            sorted_items = sorted(items, key=lambda i: i.name)
            self._instances[spec_name] = sorted_items
            for inst in sorted_items:
                self._instance_spec[inst.id] = spec_name
        return self._instances[spec_name]

    def measurements(self, instance_id: str) -> list[CellMeasurement]:
        """Return all measurements for a cell instance, sorted by name.

        Parameters
        ----------
        instance_id : str
            Cell-instance ID.

        Returns
        -------
        list[CellMeasurement]
            All measurements belonging to the instance, paginated under the
            hood and cached for subsequent calls.
        """
        if instance_id not in self._measurements:
            items = self._collect_all_pages(
                lambda limit, offset: self.client.cell_measurement.list(
                    instance_id, limit=limit, offset=offset
                )
            )
            sorted_items = sorted(items, key=lambda m: m.name)
            self._measurements[instance_id] = sorted_items
            for m in sorted_items:
                self._measurement_instance[m.id] = instance_id
        return self._measurements[instance_id]

    def _collect_all_pages(self, fetch_page: Callable[[int, int], Any]) -> list[Any]:
        """Page through a paginated SDK endpoint and return every item.

        Advances ``offset`` by the actual number of items returned (not by
        ``page_size``) and stops when a page yields no items, so an empty
        intermediate page can't cause an infinite loop.
        """
        items: list[Any] = []
        offset = 0
        while True:
            page = fetch_page(self.page_size, offset)
            if not page.items:
                break
            items.extend(page.items)
            if len(items) >= page.total:
                break
            offset += len(page.items)
        return items

    def steps(self, measurement_id: str) -> DataFrame:
        """Return the step summary DataFrame for a measurement (cached).

        Parameters
        ----------
        measurement_id : str
            Cell-measurement ID.

        Returns
        -------
        DataFrame
            Step summary in the currently configured DataFrame backend
            (pandas or polars; see :func:`set_dataframe_backend`).
        """
        if measurement_id not in self._steps:
            self._steps[measurement_id] = self.client.cell_measurement.steps(
                measurement_id
            )
        return self._steps[measurement_id].copy()

    def time_series(self, measurement_id: str) -> DataFrame:
        """Return the time-series DataFrame for a measurement (cached).

        Parameters
        ----------
        measurement_id : str
            Cell-measurement ID.

        Returns
        -------
        DataFrame
            Time-series data in the currently configured DataFrame backend
            (pandas or polars; see :func:`set_dataframe_backend`).
        """
        if measurement_id not in self._time_series:
            self._time_series[measurement_id] = (
                self.client.cell_measurement.time_series(measurement_id)
            )
        return self._time_series[measurement_id].copy()

    def clear(self) -> None:
        """Drop the entire cache.

        Battery data is immutable once uploaded, so the only staleness mode is
        "new sibling appeared on the platform." Call this from a long-running
        notebook to force a fresh fetch on the next access without
        re-instantiating the navigator.
        """
        self._specs = None
        self._instances.clear()
        self._measurements.clear()
        self._steps.clear()
        self._time_series.clear()
        self._instance_spec.clear()
        self._measurement_instance.clear()

    def invalidate(
        self,
        *,
        spec_name: str | None = None,
        instance_id: str | None = None,
        measurement_id: str | None = None,
    ) -> None:
        """Invalidate cached entries for a specific spec, instance, or measurement.

        Invalidation cascades downward: dropping a spec also drops its
        instances and their measurements; dropping an instance also drops its
        measurements. Calling with no arguments is equivalent to
        :meth:`clear`.

        Parameters
        ----------
        spec_name : str, optional
            Drop the cached spec listing and the instances + measurements
            belonging to this spec. The next :meth:`specs` / :meth:`spec` call
            will refetch.
        instance_id : str, optional
            Drop the cached instance listing for its parent spec and the
            measurements belonging to this instance.
        measurement_id : str, optional
            Drop the cached steps and time series for this measurement, plus
            the parent instance's measurement listing so a newly uploaded
            sibling shows up on the next call.
        """
        if spec_name is None and instance_id is None and measurement_id is None:
            self.clear()
            return

        if spec_name is not None:
            self._specs = None
            for inst in self._instances.pop(spec_name, []):
                self._invalidate_instance_downstream(inst.id)

        if instance_id is not None:
            parent_spec = self._instance_spec.get(instance_id)
            if parent_spec is not None:
                self._instances.pop(parent_spec, None)
            self._invalidate_instance_downstream(instance_id)

        if measurement_id is not None:
            self._steps.pop(measurement_id, None)
            self._time_series.pop(measurement_id, None)
            parent_inst = self._measurement_instance.pop(measurement_id, None)
            if parent_inst is not None:
                self._measurements.pop(parent_inst, None)

    def _invalidate_instance_downstream(self, instance_id: str) -> None:
        for m in self._measurements.pop(instance_id, []):
            self._steps.pop(m.id, None)
            self._time_series.pop(m.id, None)
            self._measurement_instance.pop(m.id, None)
        self._instance_spec.pop(instance_id, None)
