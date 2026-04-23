"""Cell measurement client for managing battery cell test data.

This module provides the :class:`CellMeasurementClient` for uploading,
retrieving, and managing measurement data from battery cell testing. It
supports time series data (signed URL upload, redirect-based download),
file measurements (signed URL upload, redirect-based download), and
properties measurements (direct JSON).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import io
from typing import Any

import pandas as pd
import polars as pl
import requests

from .cache import _load_from_cache, _save_to_cache
from .errors import IonworksError
from .models import (
    CellMeasurement,
    CellMeasurementBundleResponse,
    CellMeasurementDetail,
    InitiateUploadResponse,
    MeasurementType,
    PaginatedList,
    StepsAndCycles,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)
from .validators import (
    DataFrame,
    df_to_dict_validator,
    dict_to_df_validator,
    get_dataframe_backend,
    validate_measurement_data,
)


def _convert_cached_df(value: pl.DataFrame | None) -> DataFrame | None:
    """Convert a cached polars DataFrame to the active DataFrame backend."""
    if value is None:
        return None
    if get_dataframe_backend() == "pandas":
        return value.to_pandas()
    return value


def _normalize_url(url: str) -> str:
    """Replace Docker-internal hostname with localhost for local development."""
    return url.replace("host.docker.internal", "localhost")


def _to_polars(df: DataFrame | dict) -> pl.DataFrame:
    """Convert pandas DataFrame or dict to polars DataFrame.

    Parameters
    ----------
    df : DataFrame | dict
        Input data as pandas DataFrame, polars DataFrame, or dict.

    Returns
    -------
    pl.DataFrame
        Polars DataFrame.
    """
    if isinstance(df, pl.DataFrame):
        return df
    if isinstance(df, pd.DataFrame):
        return pl.from_pandas(df)
    if isinstance(df, dict):
        return pl.DataFrame(df)
    raise TypeError(f"Expected DataFrame or dict, got {type(df).__name__}")


def _parquet_bytes_to_df(content: bytes) -> DataFrame:
    """Deserialize parquet bytes to a DataFrame using the active backend."""
    df = pl.read_parquet(io.BytesIO(content))
    if get_dataframe_backend() == "pandas":
        return df.to_pandas()
    return df


def _build_measurement_payload(
    name: str,
    notes: str | None = None,
    protocol: dict[str, Any] | None = None,
    start_time: str | None = None,
    test_setup: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a measurement metadata dict, omitting None-valued optional fields.

    Parameters
    ----------
    name : str
        Measurement name (required).
    notes : str | None, optional
        Optional notes.
    protocol : dict[str, Any] | None, optional
        Protocol information.
    start_time : str | None, optional
        ISO-formatted start time.
    test_setup : dict[str, Any] | None, optional
        Test setup metadata.
    **extra : Any
        Additional fields to include unconditionally.

    Returns
    -------
    dict[str, Any]
        Payload dict with only non-None values.
    """
    payload: dict[str, Any] = {"name": name, **extra}
    if notes is not None:
        payload["notes"] = notes
    if protocol is not None:
        payload["protocol"] = protocol
    if start_time is not None:
        payload["start_time"] = start_time
    if test_setup is not None:
        payload["test_setup"] = test_setup
    return payload


class CellMeasurementClient:
    """Client for managing cell measurement data."""

    #: Default timeout for signed URL uploads as (connect, read) in seconds.
    UPLOAD_TIMEOUT: tuple[float, float] = (10, 300)

    def __init__(self, client: Any) -> None:
        """Initialize the CellMeasurementClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API calls.
        """
        self.client = client

    def list(
        self,
        cell_instance_id: str,
        limit: int | None = None,
        offset: int | None = None,
        measurement_type: str | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        created_by_email: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[CellMeasurement]:
        """List cell measurements for a cell instance with optional filtering.

        Always returns a :class:`PaginatedList` which behaves like a regular
        ``list``. Use ``limit`` and ``offset`` to control the page.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the cell instance.
        limit : int | None, optional
            Maximum number of measurements to return per page.
        offset : int | None, optional
            Number of measurements to skip for pagination.
        measurement_type : str | None, optional
            Filter by measurement type (e.g. ``"time_series"``, ``"file"``,
            ``"properties"``). If None, returns all types.
        name : str | None, optional
            Case-insensitive substring match on measurement name.
        name_exact : str | None, optional
            Exact match on measurement name. Takes precedence over ``name``.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        started_after : str | None, optional
            ISO datetime; return measurements started after this time.
        started_before : str | None, optional
            ISO datetime; return measurements started before this time.
        created_after : str | None, optional
            ISO datetime; return measurements created after this time.
        created_before : str | None, optional
            ISO datetime; return measurements created before this time.
        updated_after : str | None, optional
            ISO datetime; return measurements updated after this time.
        updated_before : str | None, optional
            ISO datetime; return measurements updated before this time.
        order_by : str | None, optional
            Column to sort by (``"name"``, ``"created_at"``, ``"updated_at"``,
            ``"start_time"``).
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[CellMeasurement]
            All measurements for the cell instance.
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
        # Add measurement-specific filters
        if started_after is not None:
            filter_params["start_time_gt"] = started_after
        if started_before is not None:
            filter_params["start_time_lt"] = started_before

        endpoint = _build_endpoint(
            f"/cell_instances/{cell_instance_id}/cell_measurements",
            {
                "limit": limit,
                "offset": offset,
                "measurement_type": measurement_type,
                **filter_params,
            },
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, CellMeasurement)

    def get(self, measurement_id: str) -> CellMeasurement:
        """Get a specific cell measurement by its ID only."""
        endpoint = f"/cell_measurements/{measurement_id}"
        response_data = self.client.get(endpoint)
        return CellMeasurement(**response_data)

    def detail(
        self,
        measurement_id: str,
        use_cache: bool = True,
        include_steps: bool = True,
        include_cycles: bool = True,
        include_time_series: bool = True,
    ) -> CellMeasurementDetail:
        """Fetch measurement data.

        Automatically adapts to the measurement type:

        - **time_series**: fetches steps, cycles, and time series data
        - **file**: fetches signed download URLs for files
        - **properties**: returns properties from the measurement metadata

        Uses flat endpoints with parallel requests and downloads time series
        and steps via signed URL.

        Use the ``include_*`` flags to skip fetching data you don't need,
        which avoids unnecessary requests. These flags only apply to
        time_series-type measurements.

        Parameters
        ----------
        measurement_id : str
            The ID of the cell measurement.
        use_cache : bool, optional
            If True (default), check the local file cache before making API
            calls and store results for future use. Applies to time_series
            measurements (steps, cycles, and time series data). File URLs are
            never cached because signed URLs are short-lived.
        include_steps : bool, optional
            Whether to fetch step data. Defaults to True.
            Only applies to time_series measurements.
        include_cycles : bool, optional
            Whether to fetch cycle metrics. Defaults to True.
            Only applies to time_series measurements.
        include_time_series : bool, optional
            Whether to fetch time series data. Defaults to
            True. Only applies to time_series measurements.

        Returns
        -------
        CellMeasurementDetail
            Measurement details with requested data fields.
            Fields not requested will be None.
        """
        base = f"/cell_measurements/{measurement_id}"

        # Always fetch metadata first to determine measurement type
        metadata = self.client.get(base)
        measurement = CellMeasurement(**metadata)
        measurement_type = measurement.measurement_type

        if measurement_type == MeasurementType.file:
            return self._detail_file(measurement, use_cache=use_cache)

        if measurement_type == MeasurementType.properties:
            return CellMeasurementDetail(
                **measurement.model_dump(),
                instance_id=measurement.cell_instance_id,
            )

        # time_series: fetch data in parallel
        return self._detail_time_series(
            measurement,
            use_cache=use_cache,
            include_steps=include_steps,
            include_cycles=include_cycles,
            include_time_series=include_time_series,
        )

    def _detail_time_series(
        self,
        measurement: CellMeasurement,
        use_cache: bool = True,
        include_steps: bool = True,
        include_cycles: bool = True,
        include_time_series: bool = True,
    ) -> CellMeasurementDetail:
        """Fetch time_series measurement detail with parallel requests."""
        measurement_id = measurement.id

        # Check cache for all requested keys before making any API calls
        cached = _load_from_cache(measurement_id) if use_cache else None
        have_steps = cached is not None and "steps" in cached
        have_cycles = cached is not None and "cycles" in cached
        have_ts = cached is not None and "time_series" in cached

        need_steps = include_steps and not have_steps
        need_cycles = include_cycles and not have_cycles
        need_ts = include_time_series and not have_ts

        base = f"/cell_measurements/{measurement_id}"
        futures: dict[str, Any] = {}

        with ThreadPoolExecutor(max_workers=3) as pool:
            if need_steps:
                futures["steps"] = pool.submit(
                    self.client.request_raw,
                    "GET",
                    f"{base}/steps/download",
                )
            if need_cycles:
                futures["sc"] = pool.submit(
                    self.client.get,
                    f"{base}/steps_and_cycles",
                )
            if need_ts:
                futures["ts"] = pool.submit(
                    self.client.request_raw,
                    "GET",
                    f"{base}/time_series/download",
                )

        steps = None
        cycles = None
        to_cache: dict[str, Any] = {}

        if include_steps:
            if have_steps:
                steps = _convert_cached_df(cached["steps"])
            elif "steps" in futures:
                try:
                    steps = _parquet_bytes_to_df(futures["steps"].result())
                    to_cache["steps"] = steps
                except IonworksError:
                    # Legacy measurements created before signed-URL upload
                    # was introduced may not have a ``steps.parquet`` in
                    # storage. Treat as "no steps" rather than failing the
                    # whole detail fetch. New measurements always have the
                    # file (backend writes it even for empty step lists).
                    steps = None

        if include_cycles:
            if have_cycles:
                cycles = _convert_cached_df(cached["cycles"])
            elif "sc" in futures:
                sc_result = futures["sc"].result()
                cycles = dict_to_df_validator(sc_result.get("cycles"))
                to_cache["cycles"] = cycles

        time_series = None
        if include_time_series:
            if have_ts:
                time_series = cached["time_series"]
            elif "ts" in futures:
                time_series = _parquet_bytes_to_df(futures["ts"].result())
                to_cache["time_series"] = time_series

        if use_cache and to_cache:
            _save_to_cache(measurement_id, to_cache)

        return CellMeasurementDetail(
            **measurement.model_dump(),
            instance_id=measurement.cell_instance_id,
            steps=steps,
            cycles=cycles,
            time_series=time_series,
        )

    def _detail_file(
        self,
        measurement: CellMeasurement,
        use_cache: bool = True,
    ) -> CellMeasurementDetail:
        """Fetch file measurement detail, downloading and caching file bytes."""
        files = self.download_files(measurement.id, use_cache=use_cache)
        return CellMeasurementDetail(
            **measurement.model_dump(),
            instance_id=measurement.cell_instance_id,
            files=files,
        )

    def _cached_fetch(
        self,
        measurement_id: str,
        cache_key: str,
        fetch: Callable[[], Any],
        use_cache: bool,
    ) -> Any:
        """Check the local cache, call *fetch* on miss, and store the result.

        Parameters
        ----------
        measurement_id : str
            Measurement whose data is being fetched.
        cache_key : str
            Key inside the per-measurement cache dict (e.g. ``"steps"``).
        fetch : Callable[[], Any]
            Zero-arg callable that performs the actual API/download call and
            returns the value to cache and return.
        use_cache : bool
            Whether to consult / populate the local file cache.

        Returns
        -------
        Any
            The cached value (converted via ``dict_to_df_validator`` on a cache
            hit) or the fresh result returned by *fetch* on a cache miss.
        """
        if use_cache:
            cached = _load_from_cache(measurement_id)
            if cached is not None and cache_key in cached:
                return _convert_cached_df(cached[cache_key])

        result = fetch()

        if use_cache:
            _save_to_cache(measurement_id, {cache_key: result})

        return result

    def steps(self, measurement_id: str, use_cache: bool = True) -> DataFrame:
        """Download step data via redirect.

        Downloads the raw parquet file directly from storage.

        Parameters
        ----------
        measurement_id : str
            The ID of the cell measurement.
        use_cache : bool, optional
            If True (default), check the local file cache before making an API
            call and store the result for future use.

        Returns
        -------
        DataFrame
            Step data (polars or pandas based on config).
        """

        def fetch() -> DataFrame:
            content = self.client.request_raw(
                "GET",
                f"/cell_measurements/{measurement_id}/steps/download",
            )
            return _parquet_bytes_to_df(content)

        return self._cached_fetch(measurement_id, "steps", fetch, use_cache)

    def cycles(self, measurement_id: str, use_cache: bool = True) -> DataFrame:
        """Get cycle metrics for a measurement.

        Parameters
        ----------
        measurement_id : str
            The ID of the cell measurement.
        use_cache : bool, optional
            If True (default), check the local file cache before making an API
            call and store the result for future use.

        Returns
        -------
        DataFrame
            Cycle metrics (polars or pandas based on config).
        """

        def fetch() -> DataFrame:
            data = self.client.get(f"/cell_measurements/{measurement_id}/cycles")
            return dict_to_df_validator(data["cycles"])

        return self._cached_fetch(measurement_id, "cycles", fetch, use_cache)

    def steps_and_cycles(
        self, measurement_id: str, use_cache: bool = True
    ) -> StepsAndCycles:
        """Get steps and cycles in one call.

        More efficient than calling :meth:`steps` and :meth:`cycles` separately
        since cycles are derived from steps on the server.

        Parameters
        ----------
        measurement_id : str
            The ID of the cell measurement.
        use_cache : bool, optional
            If True (default), check the local file cache before making an API
            call and store the result for future use.

        Returns
        -------
        StepsAndCycles
            Object with ``steps`` and ``cycles`` DataFrames.
        """
        if use_cache:
            cached = _load_from_cache(measurement_id)
            if cached is not None and "steps" in cached and "cycles" in cached:
                return StepsAndCycles(
                    steps=_convert_cached_df(cached["steps"]),
                    cycles=_convert_cached_df(cached["cycles"]),
                )

        endpoint = f"/cell_measurements/{measurement_id}/steps_and_cycles"
        data = self.client.get(endpoint)
        result = StepsAndCycles(**data)

        if use_cache:
            _save_to_cache(
                measurement_id,
                {"steps": result.steps, "cycles": result.cycles},
            )

        return result

    def time_series(self, measurement_id: str, use_cache: bool = True) -> DataFrame:
        """Download full time series as a parquet file.

        The API endpoint redirects to storage; the HTTP client follows the
        redirect automatically and returns the raw parquet bytes.

        Parameters
        ----------
        measurement_id : str
            The ID of the cell measurement.
        use_cache : bool, optional
            If True (default), check the local file cache before making an API
            call and store the result for future use.

        Returns
        -------
        DataFrame
            Full time series data.
        """

        def fetch() -> DataFrame:
            content = self.client.request_raw(
                "GET", f"/cell_measurements/{measurement_id}/time_series/download"
            )
            return _parquet_bytes_to_df(content)

        return self._cached_fetch(measurement_id, "time_series", fetch, use_cache)

    def update(self, measurement_id: str, data: dict[str, Any]) -> CellMeasurement:
        """Update an existing cell measurement.

        Parameters
        ----------
        measurement_id : str
            The ID of the cell measurement to update.
        data : dict[str, Any]
            Dictionary containing the fields to update.

        Returns
        -------
        CellMeasurement
            The updated cell measurement.
        """
        endpoint = f"/cell_measurements/{measurement_id}"
        response_data = self.client.patch(endpoint, data)
        return CellMeasurement(**response_data)

    def delete(self, measurement_id: str) -> None:
        """Delete a cell measurement by measurement ID only."""
        endpoint = f"/cell_measurements/{measurement_id}"
        self.client.delete(endpoint)

    def _dataframe_to_parquet(self, df: pl.DataFrame) -> bytes:
        """Convert a polars DataFrame to parquet bytes.

        Parameters
        ----------
        df : pl.DataFrame
            The DataFrame to convert.

        Returns
        -------
        bytes
            Parquet file content (uses zstd compression).
        """
        buffer = io.BytesIO()
        df.write_parquet(buffer, compression="zstd")
        return buffer.getvalue()

    def _initiate_upload(
        self,
        cell_instance_id: str,
        name: str,
        notes: str | None = None,
    ) -> InitiateUploadResponse:
        """Initiate a signed URL upload for a new measurement.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the cell instance.
        name : str
            Name for the new measurement.
        notes : str | None
            Optional notes for the measurement.

        Returns
        -------
        InitiateUploadResponse
            Response containing measurement_id and uploads list.
        """
        endpoint = (
            f"/cell_instances/{cell_instance_id}/cell_measurements/initiate-upload"
        )
        payload = {"measurement": _build_measurement_payload(name, notes=notes)}
        response_data = self.client.post(endpoint, payload)
        return InitiateUploadResponse(**response_data)

    def _upload_to_signed_url(
        self,
        signed_url: str,
        data: bytes | io.IOBase,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload data to a signed URL.

        Parameters
        ----------
        signed_url : str
            The signed URL to upload to.
        data : bytes | io.IOBase
            The file content as bytes or an open file handle.
        content_type : str, optional
            MIME type for the upload. Defaults to "application/octet-stream".

        Raises
        ------
        IonworksError
            If the upload fails.
        """
        url = _normalize_url(signed_url)

        try:
            response = requests.put(
                url,
                data=data,
                headers={"Content-Type": content_type},
                timeout=self.UPLOAD_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise IonworksError(f"Failed to upload to signed URL: {e}") from None

    def _confirm_upload(
        self,
        measurement_id: str,
        cell_instance_id: str,
        measurement_data: dict[str, Any],
        measurement_type: str | MeasurementType = MeasurementType.time_series,
        filenames: list[str] | None = None,
    ) -> CellMeasurementBundleResponse:
        """Confirm a signed URL upload after successful file upload.

        This creates the measurement record in the database. The measurement
        data must be passed again (same as initiate) to ensure no orphaned
        records are created if the file upload fails.

        Parameters
        ----------
        measurement_id : str
            The pre-generated ID for the measurement (from initiate).
        cell_instance_id : str
            The ID of the parent cell instance.
        measurement_data : dict[str, Any]
            Measurement metadata (name, notes, etc.) — same as passed to
            initiate.
        measurement_type : str
            Type of measurement being confirmed. Defaults to "time_series".
        filenames : list[str] | None
            Filenames that were uploaded.  Passed to the backend so it can
            persist them in ``file_metadata`` and skip the slow storage
            listing.

        Returns
        -------
        CellMeasurementBundleResponse
            Response containing measurement fields and steps_created.
        """
        endpoint = f"/cell_measurements/{measurement_id}/confirm-upload"
        payload: dict[str, Any] = {
            "cell_instance_id": cell_instance_id,
            "measurement": measurement_data,
            "measurement_type": measurement_type,
        }
        if filenames:
            payload["filenames"] = filenames

        response_data = self.client.post(endpoint, payload)
        response_data.pop("instance", None)
        return CellMeasurementBundleResponse(**response_data)

    def create(
        self,
        cell_instance_id: str,
        measurement_detail: dict[str, Any],
        validate_strict: bool = False,
        rated_capacity: float | None = None,
        voltage_window: tuple[float, float] | None = None,
    ) -> CellMeasurementBundleResponse:
        """Create a new cell measurement with steps and time series data.

        Uses signed URL upload for better performance with large datasets.
        Data is uploaded directly to storage as parquet, bypassing backend
        JSON parsing. No database record is created until the upload is
        confirmed, preventing orphaned records if upload fails.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the cell instance to create the measurement for.
        measurement_detail : dict[str, Any]
            Dictionary containing 'measurement', 'steps', and 'time_series'.

            - measurement: dict with 'name' (required) and 'notes'
            - time_series: pandas DataFrame or dict with time series data
            - steps: optional dict with pre-calculated steps data
        validate_strict : bool, optional
            If False (default), runs only the always-on checks (positive
            current convention, time starts at 0, time monotonic, step
            count sequential, cumulative capacity/energy reset per step).
            If True, additionally runs: minimum points per step, cycle
            constant within step, time-gap check (> 5 h), and — when the
            corresponding inputs are provided — voltage continuity and
            consecutive-full-step / per-step capacity checks.
        rated_capacity : float, optional
            Rated (nominal) cell capacity in A.h. Used only when
            ``validate_strict=True`` together with a ``steps`` entry in
            ``measurement_detail``; enables the consecutive same-direction
            full-step check (hard) and the per-step capacity 500 % soft
            warning.
        voltage_window : tuple[float, float], optional
            Rated ``(V_min, V_max)`` voltage window of the cell. Used only
            when ``validate_strict=True``; enables the voltage-continuity
            check that detects time series rows that are out of
            chronological order.

        Returns
        -------
        CellMeasurementBundleResponse
            Response containing the created measurement, steps count, and
            file path.

        Raises
        ------
        MeasurementValidationError
            If data validation fails. Non-strict checks cover the sign
            convention, time axis, step-count monotonicity, and cumulative
            reset. Strict-only checks additionally cover time gaps,
            voltage continuity, and consecutive same-direction full steps.
        """
        measurement_info = measurement_detail["measurement"]
        name = measurement_info["name"]
        notes = measurement_info.get("notes")

        # Step 1: Convert time_series to polars DataFrame
        # Accepts pandas DataFrame, polars DataFrame, or dict
        time_series = _to_polars(measurement_detail["time_series"])
        raw_steps = measurement_detail.get("steps")
        steps_df_for_validation = (
            _to_polars(raw_steps) if raw_steps is not None else None
        )

        # Step 2: Validate the data before any upload
        data_type = measurement_info.get("data_type")
        validate_measurement_data(
            time_series,
            strict=validate_strict,
            data_type=data_type,
            steps_df=steps_df_for_validation,
            rated_capacity=rated_capacity,
            voltage_window=voltage_window,
        )

        # Step 3: Initiate upload (validates metadata, returns signed URL, no DB record)
        initiate_result = self._initiate_upload(cell_instance_id, name, notes)

        # Step 4: Convert to parquet
        parquet_bytes = self._dataframe_to_parquet(time_series)

        # Step 5: Upload time series to signed URL
        ts_upload = next(
            (u for u in initiate_result.uploads if u.filename == "time_series.parquet"),
            None,
        )
        if ts_upload is None:
            raise IonworksError(
                "No time_series.parquet upload URL returned from server"
            )
        self._upload_to_signed_url(ts_upload.signed_url, parquet_bytes)

        # Step 6: Upload steps if provided
        steps = measurement_detail.get("steps")
        if steps is not None:
            steps_upload = next(
                (u for u in initiate_result.uploads if u.filename == "steps.parquet"),
                None,
            )
            if steps_upload is None:
                raise IonworksError("No steps.parquet upload URL returned from server")
            steps_pl = _to_polars(df_to_dict_validator(steps))
            steps_parquet = self._dataframe_to_parquet(steps_pl)
            self._upload_to_signed_url(steps_upload.signed_url, steps_parquet)

        # Step 7: Confirm upload (creates the measurement record)
        api_measurement_info = {
            k: v for k, v in measurement_info.items() if k != "data_type"
        }
        return self._confirm_upload(
            measurement_id=initiate_result.measurement_id,
            cell_instance_id=cell_instance_id,
            measurement_data=api_measurement_info,
        )

    def create_or_get(
        self,
        cell_instance_id: str,
        measurement_detail: dict[str, Any],
        validate_strict: bool = False,
        rated_capacity: float | None = None,
        voltage_window: tuple[float, float] | None = None,
    ) -> CellMeasurement:
        """Create a new cell measurement or get existing.

        Always returns a :class:`CellMeasurement` regardless
        of whether the measurement was newly created or
        already existed.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the cell instance.
        measurement_detail : dict[str, Any]
            Dictionary containing ``measurement`` and
            ``time_series`` (same as :meth:`create`).
        validate_strict : bool, optional
            If False (default), skips strict validation.
            If True, runs strict validation including
            minimum points per step.
        rated_capacity : float, optional
            Rated (nominal) cell capacity in A.h. Forwarded to
            :meth:`create` to enable the consecutive-full-step and
            per-step capacity checks.
        voltage_window : tuple[float, float], optional
            Rated ``(V_min, V_max)`` voltage window. Forwarded to
            :meth:`create` to enable the voltage-continuity check.

        Returns
        -------
        CellMeasurement
            The measurement (newly created or existing).
        """
        try:
            bundle = self.create(
                cell_instance_id,
                measurement_detail,
                validate_strict,
                rated_capacity=rated_capacity,
                voltage_window=voltage_window,
            )
            return CellMeasurement(**bundle.model_dump(exclude={"steps_created"}))
        except IonworksError as e:
            if e.error_code == "CONFLICT" or e.status_code == 409:
                # Try to get existing measurement by ID from error detail
                if e.data is not None:
                    detail = e.data.get("detail", {})
                    existing_id = (
                        detail.get("existing_id") if isinstance(detail, dict) else None
                    )
                    if existing_id:
                        return self.get(existing_id)
                # Fall back to listing and matching by name
                measurement_name = measurement_detail["measurement"]["name"]
                measurements = self.list(cell_instance_id)
                for m in measurements:
                    if m.name == measurement_name:
                        return m
                raise ValueError(
                    f"Measurement "
                    f"'{measurement_name}' reported "
                    f"as duplicate but could not be "
                    f"found"
                ) from e
            raise

    # --- Properties Measurements --- #

    def create_properties(
        self,
        cell_instance_id: str,
        name: str,
        properties: dict[str, Any],
        *,
        notes: str | None = None,
        protocol: dict[str, Any] | None = None,
        start_time: str | None = None,
        test_setup: dict[str, Any] | None = None,
    ) -> CellMeasurement:
        """Create a properties-type measurement (no file upload).

        Use this for manual measurements like thickness, weight, or
        capacity that are stored as key-value pairs with units.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the parent cell instance.
        name : str
            Name for the measurement.
        properties : dict[str, Any]
            Key-value measurements using Quantity format for numerics.
            Example: ``{"thickness": {"value": 0.52, "unit": "mm"}}``.
        notes : str | None, optional
            Optional notes for the measurement.
        protocol : dict[str, Any] | None, optional
            Protocol information.
        start_time : str | None, optional
            ISO-formatted start time for the measurement.
        test_setup : dict[str, Any] | None, optional
            Test setup metadata.

        Returns
        -------
        CellMeasurement
            The created measurement.
        """
        endpoint = f"/cell_instances/{cell_instance_id}/cell_measurements/create"
        payload = _build_measurement_payload(
            name,
            notes=notes,
            protocol=protocol,
            start_time=start_time,
            test_setup=test_setup,
            measurement_type=MeasurementType.properties.value,
            properties=properties,
        )
        response_data = self.client.post(endpoint, payload)
        return CellMeasurement(**response_data)

    # --- File Measurements --- #

    #: Allowed image file extensions for upload.
    ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
        {"jpg", "jpeg", "png", "gif", "webp", "tiff", "bmp"}
    )

    #: Magic byte signatures by extension, used for client-side content validation.
    _IMAGE_SIGNATURES: dict[str, list[bytes]] = {
        "jpg": [b"\xff\xd8\xff"],
        "jpeg": [b"\xff\xd8\xff"],
        "png": [b"\x89PNG\r\n\x1a\n"],
        "gif": [b"GIF87a", b"GIF89a"],
        "webp": [b"RIFF"],  # Full check: RIFF....WEBP
        "tiff": [b"II\x2a\x00", b"MM\x00\x2a"],
        "bmp": [b"BM"],
    }

    #: MIME types by extension for setting Content-Type on uploads.
    _IMAGE_MIME_TYPES: dict[str, str] = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "tiff": "image/tiff",
        "bmp": "image/bmp",
    }

    @classmethod
    def _validate_image_content(cls, filepath: str, data: bytes) -> None:
        """Validate that file content matches its image extension.

        Parameters
        ----------
        filepath : str
            Path to the file (used for extension detection and error messages).
        data : bytes
            File content to validate.

        Raises
        ------
        IonworksError
            If the file extension is not allowed or the file content does not
            match the expected image format.
        """
        import os

        ext = os.path.splitext(filepath)[1].lstrip(".").lower()
        if ext not in cls.ALLOWED_IMAGE_EXTENSIONS:
            raise IonworksError(
                f"File '{os.path.basename(filepath)}' has unsupported "
                f"extension '.{ext}'. Allowed: "
                f"{', '.join(sorted(cls.ALLOWED_IMAGE_EXTENSIONS))}"
            )

        # WebP needs a special check: bytes 0-3 = RIFF, bytes 8-11 = WEBP
        if ext == "webp":
            if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
                raise IonworksError(
                    f"File '{os.path.basename(filepath)}' does not appear to "
                    f"be a valid WEBP image. The file content does not match "
                    f"the expected format."
                )
            return

        signatures = cls._IMAGE_SIGNATURES.get(ext, [])
        if signatures and not any(data.startswith(sig) for sig in signatures):
            raise IonworksError(
                f"File '{os.path.basename(filepath)}' does not appear to be "
                f"a valid {ext.upper()} image. The file content does not match "
                f"the expected format."
            )

    def create_file(
        self,
        cell_instance_id: str,
        name: str,
        filepaths: list[str],
        *,
        validate_images: bool = False,
        notes: str | None = None,
        protocol: dict[str, Any] | None = None,
        start_time: str | None = None,
        test_setup: dict[str, Any] | None = None,
    ) -> CellMeasurementBundleResponse:
        """Create a file-type measurement by uploading files.

        Uses a three-step signed URL flow: initiate (get signed URLs),
        upload each file, then confirm (create DB record). Supports any
        file type (PDFs, numpy arrays, images, etc.).

        Set ``validate_images=True`` to opt into client-side image
        validation when uploading image files specifically.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the parent cell instance.
        name : str
            Name for the measurement.
        filepaths : list[str]
            Local file paths to upload.
        validate_images : bool, optional
            When True, validate that each file is a real image with an
            allowed extension before uploading. Defaults to False.
        notes : str | None, optional
            Optional notes for the measurement.
        protocol : dict[str, Any] | None, optional
            Protocol information.
        start_time : str | None, optional
            ISO-formatted start time for the measurement.
        test_setup : dict[str, Any] | None, optional
            Test setup metadata.

        Returns
        -------
        CellMeasurementBundleResponse
            Response containing the created measurement and steps_created (0).

        Raises
        ------
        IonworksError
            If ``validate_images`` is True and any file is not a valid image,
            or if upload fails.
        FileNotFoundError
            If any file path does not exist.
        """
        import os

        # Step 1: Validate all files exist (and optionally validate image content)
        _MAX_SIGNATURE_LEN = 16
        for filepath in filepaths:
            if not os.path.isfile(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")
            if validate_images:
                with open(filepath, "rb") as f:
                    header = f.read(_MAX_SIGNATURE_LEN)
                self._validate_image_content(filepath, header)

        filenames = [os.path.basename(fp) for fp in filepaths]

        # Step 2: Initiate upload (get signed URLs for each file)
        initiate_result = self._initiate_file_upload(
            cell_instance_id, name, filenames, notes=notes
        )

        # Step 3: Upload files in parallel
        def _upload_one(upload_info: Any, filepath: str) -> None:
            ext = os.path.splitext(filepath)[1].lstrip(".").lower()
            content_type = self._IMAGE_MIME_TYPES.get(ext, "application/octet-stream")
            with open(filepath, "rb") as f:
                self._upload_to_signed_url(
                    upload_info.signed_url, f, content_type=content_type
                )

        with ThreadPoolExecutor(
            max_workers=min(len(initiate_result.uploads), 4)
        ) as pool:
            futures = [
                pool.submit(_upload_one, info, fp)
                for info, fp in zip(initiate_result.uploads, filepaths, strict=True)
            ]
            for future in futures:
                future.result()  # raises on first failure

        # Step 4: Confirm upload (creates DB record)
        return self._confirm_upload(
            measurement_id=initiate_result.measurement_id,
            cell_instance_id=cell_instance_id,
            measurement_data=_build_measurement_payload(
                name,
                notes=notes,
                protocol=protocol,
                start_time=start_time,
                test_setup=test_setup,
            ),
            measurement_type=MeasurementType.file,
            filenames=filenames,
        )

    def list_files(self, measurement_id: str) -> list[str]:
        """List filenames in a file-type measurement.

        Parameters
        ----------
        measurement_id : str
            The ID of a file-type cell measurement.

        Returns
        -------
        list[str]
            Filenames stored in the measurement.

        Raises
        ------
        IonworksError
            If the measurement is not a file type or does not exist.
        """
        endpoint = f"/cell_measurements/{measurement_id}/files"
        response_data = self.client.get(endpoint)
        return response_data["filenames"]

    def download_files(
        self,
        measurement_id: str,
        use_cache: bool = True,
        filenames: list[str] | None = None,
    ) -> dict[str, bytes]:
        """Download files for a file-type measurement.

        When ``filenames`` is provided the ``list_files`` round-trip is
        skipped, which avoids the slow storage-listing endpoint.  Use this
        when you already know the filenames (e.g. from a previous
        ``list_files`` call or from the upload response).

        Parameters
        ----------
        measurement_id : str
            The ID of a file-type cell measurement.
        use_cache : bool, optional
            If True (default), check the local file cache before downloading
            and store results for future use.
        filenames : list[str] | None, optional
            Explicit list of filenames to download.  When provided, the
            ``GET /cell_measurements/{id}/files`` listing call is skipped.

        Returns
        -------
        dict[str, bytes]
            Mapping of filename to file content bytes.

        Raises
        ------
        IonworksError
            If the measurement is not a file type, does not exist, or a
            download fails.
        """
        if use_cache:
            cached = _load_from_cache(measurement_id)
            if cached is not None and "files" in cached:
                return cached["files"]

        if filenames is None:
            filenames = self.list_files(measurement_id)
        if not filenames:
            return {}

        def _download_one(filename: str) -> tuple[str, bytes]:
            return filename, self.get_file(measurement_id, filename)

        with ThreadPoolExecutor(max_workers=min(len(filenames), 4)) as pool:
            results = list(pool.map(_download_one, filenames))

        result = dict(results)

        if use_cache:
            _save_to_cache(measurement_id, {"files": result})

        return result

    def get_file(
        self,
        measurement_id: str,
        filename: str,
    ) -> bytes:
        """Download a single file from a file-type measurement.

        This is a convenience wrapper that fetches one file directly without
        any listing call, making it the fastest way to retrieve a known file.

        Parameters
        ----------
        measurement_id : str
            The ID of a file-type cell measurement.
        filename : str
            Name of the file to download.

        Returns
        -------
        bytes
            Raw file content.

        Raises
        ------
        IonworksError
            If the measurement or file does not exist.
        """
        return self.client.request_raw(
            "GET",
            f"/cell_measurements/{measurement_id}/files/{filename}",
        )

    def _initiate_file_upload(
        self,
        cell_instance_id: str,
        name: str,
        filenames: list[str],
        notes: str | None = None,
    ) -> InitiateUploadResponse:
        """Initiate a signed URL upload for files.

        Parameters
        ----------
        cell_instance_id : str
            The ID of the cell instance.
        name : str
            Name for the new measurement.
        filenames : list[str]
            List of filenames to upload.
        notes : str | None
            Optional notes for the measurement.

        Returns
        -------
        InitiateUploadResponse
            Response containing measurement_id and signed URL info per file.
        """
        endpoint = (
            f"/cell_instances/{cell_instance_id}/cell_measurements/initiate-upload"
        )
        payload: dict[str, Any] = {
            "measurement": _build_measurement_payload(name, notes=notes),
            "measurement_type": "file",
            "filenames": filenames,
        }
        response_data = self.client.post(endpoint, payload)
        return InitiateUploadResponse(**response_data)
