"""ECM (Equivalent Circuit Model) parameterization client.

Authenticated ECM fits run as **background jobs** on the platform's worker
fleet — each ``fit_*`` call returns immediately with a job handle. Use
:py:meth:`ECMClient.wait_for_completion` to block until the result is ready,
or poll ``client.job.get(job_id)`` directly.

The unauthenticated demo endpoint (:py:meth:`ECMClient.fit_from_example`)
remains synchronous and rate-limited.

Three input modes are supported:

- :py:meth:`ECMClient.fit_from_example` — built-in demo dataset, public.
- :py:meth:`ECMClient.fit_from_file` — upload a local cycler file (CSV,
  parquet, or any format ionworksdata can detect).
- :py:meth:`ECMClient.fit_from_measurements` — fit data already stored as
  cell measurements in the platform.

Use :py:meth:`ECMClient.save_to_project` to persist a fit result as a
Parameterized Model linked to a cell specification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import time
from typing import Any, BinaryIO, cast

from pydantic import BaseModel, Field, ValidationError, field_validator

from .errors import IonworksError


class RcPairFit(BaseModel):
    """One RC pair fit result."""

    r: list[float] = Field(description="Resistance values [Ohm].")
    c: list[float] = Field(description="Capacitance values [F].")
    tau: list[float] = Field(description="Time constants [s] (= r * c).")


class FitResults(BaseModel):
    """ECM fit results, as stored on the completed job's ``result`` field."""

    rmse_mV: float = Field(description="Root-mean-square error in millivolts.")
    num_rcs: int = Field(description="Number of RC pairs in the fit.")
    time: list[float] = Field(description="Downsampled time grid for plotting [s].")
    data_voltage: list[float] = Field(
        description="Measured voltage at the downsampled grid [V]."
    )
    model_voltage: list[float] = Field(
        description="Model voltage at the downsampled grid [V]."
    )
    soc: list[float] = Field(description="State-of-charge grid (0–1).")
    ocv: list[float] = Field(description="Open-circuit voltage curve [V].")
    r0: list[float] = Field(description="Series resistance curve [Ohm].")
    rc_pairs: list[RcPairFit] = Field(
        default_factory=list,
        description=(
            "Per-RC-pair parameter curves. Empty for unauthenticated demo fits."
        ),
    )
    ocv_provided: bool = Field(
        default=False,
        description="True if the input data carried an OCV column that was used.",
    )

    @field_validator("rc_pairs", mode="before")
    @classmethod
    def _coerce_none(cls, value: Any) -> Any:
        return [] if value is None else value

    model_config = {"extra": "allow"}


class EcmFitJob(BaseModel):
    """Handle returned when an authenticated fit is submitted."""

    job_id: str = Field(description="ID of the background fit job.")
    status: str = Field(description="Initial job status (typically 'pending').")


class OcvSocCurve(BaseModel):
    """Open-circuit voltage as a function of state of charge."""

    soc: list[float] = Field(
        ..., min_length=2, description="Strictly increasing SOC knot values in [0, 1]."
    )
    ocv: list[float] = Field(
        ..., min_length=2, description="OCV values in volts at each SOC knot."
    )

    @field_validator("ocv")
    @classmethod
    def _len_match(cls, v: list[float], info) -> list[float]:
        soc = info.data.get("soc")
        if soc is not None and len(v) != len(soc):
            raise ValueError("soc and ocv must be the same length.")
        return v

    @field_validator("soc")
    @classmethod
    def _soc_valid(cls, v: list[float]) -> list[float]:
        if any(v[i + 1] <= v[i] for i in range(len(v) - 1)):
            raise ValueError("soc values must be strictly increasing.")
        if v[0] < 0.0 or v[-1] > 1.0:
            raise ValueError("soc values must lie in [0, 1].")
        return v


class CapacityBounds(BaseModel):
    """Search bounds for capacity fitting (Ah)."""

    lo: float = Field(..., gt=0.0, description="Lower bound (Ah).")
    hi: float = Field(..., gt=0.0, description="Upper bound (Ah).")

    @field_validator("hi")
    @classmethod
    def _hi_gt_lo(cls, v: float, info) -> float:
        lo = info.data.get("lo")
        if lo is not None and v <= lo:
            raise ValueError("hi must be greater than lo.")
        return v


class FitMeasurementRequest(BaseModel):
    """One measurement to include in an ECM fit."""

    id: str = Field(..., description="Measurement ID.")
    start_step: int | None = Field(
        default=None, description="Optional inclusive start step for filtering."
    )
    end_step: int | None = Field(
        default=None, description="Optional inclusive end step for filtering."
    )
    initial_soc: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Known initial SOC (0–1) at the start of this measurement. When "
            "None and an ``ocv_soc_curve`` is provided in ``ecm_options``, "
            "soc0 is auto-seeded by inverting V[s] = OCV(soc0) − I[s]·R0(soc0) "
            "after a warm-up fit. Falls back to coulomb-count for "
            "single-measurement runs without an OCV curve."
        ),
    )


class FitECMOptions(BaseModel):
    """ECM fitting hyperparameters shared across all measurements."""

    num_rcs: int = Field(default=2, ge=0, le=5, description="Number of RC pairs.")
    fit_ocv: bool = Field(
        default=True, description="Whether to fit OCV or use provided."
    )
    capacity: float | None = Field(
        default=None,
        gt=0.0,
        description="Known cell capacity in Ah. Estimated if not provided.",
    )
    ocv_soc_curve: OcvSocCurve | None = Field(
        default=None,
        description=(
            "Open-circuit voltage curve as (soc, ocv) arrays. When provided, "
            "OCV-fitting is skipped and OCV is interpolated from the curve. "
            "Mutually exclusive with measurement Open-circuit voltage [V] columns."
        ),
    )
    bounds_capacity: CapacityBounds | None = Field(
        default=None,
        description=(
            "Search bounds (Ah) for capacity fitting; only used when "
            "ocv_soc_curve is provided and capacity is None."
        ),
    )
    num_knots: int | None = Field(
        default=None,
        ge=1,
        le=64,
        description=(
            "Number of SOC knots for RC alpha + default beta resolution. "
            "Service default applies when None."
        ),
    )
    num_knots_r0: int | None = Field(
        default=None,
        ge=1,
        le=64,
        description=(
            "Number of SOC knots for R0(SoC). Service default applies when None."
        ),
    )
    knot_schedule: list[int] | None = Field(
        default=None,
        description=(
            "Multi-resolution beta knot schedule (strictly increasing positive "
            "ints ending at num_knots). Auto-derived when None."
        ),
    )
    clamp_boundary_knots: bool = Field(
        default=True,
        description=(
            "When True (default), clamp boundary R0/alpha/beta knot values to "
            "within ``clamp_max_ratio`` of their interior neighbour. Set "
            "False if you build R_rc(soc)=alpha(soc)/beta(soc) symbolically "
            "downstream — the ratio is well behaved even at unobserved "
            "boundary knots and clamping costs fit quality."
        ),
    )
    clamp_max_ratio: float = Field(
        default=10.0,
        gt=1.0,
        description=(
            "Cap boundary knot values at ``clamp_max_ratio`` × their interior "
            "neighbour (only when ``clamp_boundary_knots`` is True). Larger "
            "values preserve more SoC variation; smaller values are stricter "
            "against R_rc=alpha/beta blow-ups that destabilise pybamm forward "
            "simulations. Default 10."
        ),
    )

    @field_validator("knot_schedule")
    @classmethod
    def _schedule_valid(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("knot_schedule must be non-empty.")
        if any(k < 1 for k in v):
            raise ValueError("knot_schedule entries must be >= 1.")
        if any(v[i + 1] <= v[i] for i in range(len(v) - 1)):
            raise ValueError("knot_schedule must be strictly increasing.")
        return v


class FitFromMeasurementsRequest(BaseModel):
    """Request body for :py:meth:`ECMClient.fit_from_measurements`."""

    measurements: list[FitMeasurementRequest] = Field(
        ..., min_length=1, description="One or more measurements to fit jointly."
    )
    ecm_options: FitECMOptions = Field(
        default_factory=FitECMOptions,
        description="ECM fitting hyperparameters shared across all measurements.",
    )


class SaveToProjectResponse(BaseModel):
    """Response from :py:meth:`ECMClient.save_to_project`."""

    model_id: str = Field(description="ID of the underlying Model row.")
    parameterized_model_id: str = Field(
        description="ID of the new Parameterized Model holding the fit."
    )


class ECMClient:
    """Client for ECM parameterization.

    Authenticated fits (``fit_from_measurements``, ``fit_from_file``) submit
    background jobs and return an :class:`EcmFitJob` handle. Use
    :py:meth:`wait_for_completion` to block until the result is ready.

    The active organization is resolved from the API key — no org ID needs to
    be passed in.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    # ------------------------------------------------------------------
    # Public / unauthenticated demo endpoints
    # ------------------------------------------------------------------

    def list_examples(self) -> list[dict[str, Any]]:
        """List built-in example datasets available for ECM fitting."""
        return cast(list[dict[str, Any]], self.client.get("/ecm/examples"))

    def get_example_data(self, example_id: str) -> dict[str, Any]:
        """Return downsampled time-series for a built-in example."""
        return cast(dict[str, Any], self.client.get(f"/ecm/examples/{example_id}/data"))

    def fit_from_example(
        self,
        example_id: str,
        num_rcs: int = 2,
        fit_ocv: bool = True,
        initial_soc: float | None = None,
        capacity: float | None = None,
    ) -> FitResults:
        """Fit an ECM to a built-in example dataset (public, synchronous).

        This endpoint is rate-limited (60/min) and returns the complete fit
        directly — no job polling needed. RC-pair parameters are only
        included for authenticated callers with ECM results access.
        """
        params: dict[str, Any] = {
            "example_id": example_id,
            "num_rcs": num_rcs,
            "fit_ocv": fit_ocv,
        }
        if initial_soc is not None:
            params["initial_soc"] = initial_soc
        if capacity is not None:
            params["capacity"] = capacity
        # No file body — only query params.
        response = self.client.post_multipart("/ecm/fit", files=None, params=params)
        return FitResults(**response)

    # ------------------------------------------------------------------
    # Authenticated, async (job-backed) endpoints
    # ------------------------------------------------------------------

    def fit_from_measurements(self, config: dict[str, Any]) -> EcmFitJob:
        """Submit an ECM fit from one or more cell measurements.

        Returns immediately with a job handle. The fit runs on the worker
        fleet (typically 10–60 s). Pass the returned ``job_id`` to
        :py:meth:`wait_for_completion` or ``client.job.get(...)``.

        Parameters
        ----------
        config : dict[str, Any]
            See :class:`FitFromMeasurementsRequest`. Required key:
            ``measurements`` — a list of ``{"id": <measurement_id>, ...}``
            dicts (each may carry per-measurement ``start_step``,
            ``end_step``, ``initial_soc``). Optional ``ecm_options`` with
            shared hyperparameters (``num_rcs``, ``fit_ocv``, ``capacity``,
            ``ocv_soc_curve``, ``bounds_capacity``, ``num_knots``,
            ``num_knots_r0``, ``knot_schedule``, ``clamp_boundary_knots``,
            ``clamp_max_ratio``).

        Returns
        -------
        EcmFitJob
            Job handle (``job_id``, initial ``status``).
        """
        try:
            validated = FitFromMeasurementsRequest(**config)
        except ValidationError as e:
            raise ValueError(f"Invalid fit_from_measurements config: {e}") from e
        response = self.client.post(
            "/ecm/fit-from-measurements",
            json_payload=validated.model_dump(exclude_none=True),
        )
        return EcmFitJob(**response)

    @staticmethod
    def _open_upload(
        file: str | Path | BinaryIO, filename: str | None
    ) -> tuple[dict[str, tuple[str, BinaryIO, str]], BinaryIO | None]:
        """Build a ``files=`` multipart dict for ``file`` and return any opened handle.

        Returns
        -------
        tuple
            ``(files, opened)`` where ``files`` is the multipart payload and
            ``opened`` is the file handle the caller must close in a
            ``finally`` block (``None`` when ``file`` is already a stream).
        """
        if isinstance(file, (str, Path)):
            path = Path(file)
            opened: BinaryIO | None = path.open("rb")
            stream: BinaryIO = opened
            upload_name = filename or path.name
        else:
            opened = None
            stream = file
            upload_name = filename or getattr(file, "name", "upload.csv")
            upload_name = Path(str(upload_name)).name
        files = {"file": (upload_name, stream, "application/octet-stream")}
        return files, opened

    def fit_from_file(
        self,
        file: str | Path | BinaryIO,
        num_rcs: int = 2,
        fit_ocv: bool = True,
        initial_soc: float | None = None,
        capacity: float | None = None,
        filename: str | None = None,
    ) -> EcmFitJob:
        """Submit an ECM fit from an uploaded cycling data file.

        Returns immediately with a job handle. Accepts any format that
        ionworksdata can detect (CSV, parquet, common cycler formats).
        """
        params: dict[str, Any] = {"num_rcs": num_rcs, "fit_ocv": fit_ocv}
        if initial_soc is not None:
            params["initial_soc"] = initial_soc
        if capacity is not None:
            params["capacity"] = capacity

        files, opened = self._open_upload(file, filename)
        try:
            response = self.client.post_multipart(
                "/ecm/fit-from-file", files=files, params=params
            )
        finally:
            if opened is not None:
                opened.close()
        return EcmFitJob(**response)

    def detect_and_read(
        self,
        file: str | Path | BinaryIO,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Upload a cycling file, auto-detect format, return its time-series.

        Synchronous (no job involved) — useful for previewing a file.
        """
        files, opened = self._open_upload(file, filename)
        try:
            return cast(
                dict[str, Any],
                self.client.post_multipart("/ecm/detect-and-read", files=files),
            )
        finally:
            if opened is not None:
                opened.close()

    # ------------------------------------------------------------------
    # Job polling
    # ------------------------------------------------------------------

    def wait_for_completion(
        self,
        job: EcmFitJob | str,
        timeout: int = 300,
        poll_interval: int = 2,
        verbose: bool = True,
        raise_on_failure: bool = True,
    ) -> FitResults | None:
        """Poll an ECM-fit job until it reaches a terminal state.

        Parameters
        ----------
        job : EcmFitJob or str
            Job handle returned by ``fit_from_*`` (or a raw ``job_id``).
        timeout : int, optional
            Maximum total wait in seconds. Defaults to 300 (5 minutes).
        poll_interval : int, optional
            Seconds between polls. Defaults to 2.
        verbose : bool, optional
            Print one-line progress updates. Defaults to True.
        raise_on_failure : bool, optional
            When True (default), raise :class:`IonworksError` if the job
            fails or is canceled. When False, failed jobs return ``None``
            instead of raising — fetch the job directly via
            ``client.job.get(job_id)`` for error details.

        Returns
        -------
        FitResults or None
            Parsed fit result on success. ``None`` only when the job failed
            and ``raise_on_failure`` is False.

        Raises
        ------
        TimeoutError
            If the timeout elapses before the job reaches a terminal state.
        IonworksError
            If the job failed and ``raise_on_failure`` is True, or if it
            completed without a result payload.
        """
        job_id = job.job_id if isinstance(job, EcmFitJob) else job
        deadline = datetime.now(UTC) + timedelta(seconds=timeout)
        if verbose:
            print(f"Waiting for ECM fit job {job_id}...")

        sleep_seconds = max(float(poll_interval), 1.0)
        while datetime.now(UTC) < deadline:
            job_status = self.client.job.get(job_id)
            if job_status.is_terminal:
                if job_status.is_failed:
                    if raise_on_failure:
                        raise IonworksError(
                            f"ECM fit {job_id} {job_status.status}: "
                            f"{job_status.error or 'unknown error'}"
                        )
                    return None
                if not job_status.result:
                    raise IonworksError(
                        f"ECM fit {job_id} completed without a result payload"
                    )
                if verbose:
                    print(f"  ECM fit {job_id} completed.")
                return FitResults(**job_status.result)
            if verbose:
                print(f"  status: {job_status.status}")
            time.sleep(sleep_seconds)
            sleep_seconds = min(sleep_seconds * 1.5, 30.0)

        raise TimeoutError(
            f"ECM fit {job_id} did not complete within {timeout} seconds"
        )

    # ------------------------------------------------------------------
    # Synchronous save
    # ------------------------------------------------------------------

    def save_to_project(
        self,
        name: str,
        cell_spec_id: str,
        fit_results: FitResults | dict[str, Any],
        description: str = "",
        num_rcs: int | None = None,
    ) -> SaveToProjectResponse:
        """Persist ECM fit results as a Parameterized Model (synchronous)."""
        if isinstance(fit_results, FitResults):
            fr = fit_results
        else:
            fr = FitResults(**fit_results)

        resolved_num_rcs = num_rcs if num_rcs is not None else fr.num_rcs

        body = {
            "name": name,
            "description": description,
            "cell_spec_id": cell_spec_id,
            "num_rcs": resolved_num_rcs,
            "fit_results": {
                "soc": fr.soc,
                "ocv": fr.ocv,
                "r0": fr.r0,
                "rc_pairs": [rc.model_dump() for rc in fr.rc_pairs],
            },
        }
        response = self.client.post("/ecm/save-to-project", json_payload=body)
        return SaveToProjectResponse(**response)
