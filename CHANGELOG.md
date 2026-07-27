# Changelog — ionworks-api

All notable changes to this package are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package follows [Semantic Versioning](https://semver.org/).

For platform-wide release notes (Studio, pipeline, SDK, and more),
see [docs.ionworks.com/changelog](https://docs.ionworks.com/changelog).

<!-- New release sections are prepended below by the release-packages skill. -->

## [0.21.0] - 2026-07-24

### Added
- `simulation_settings` support (a persistent bag of pybamm mesh and solver
  settings) on the simulation, study, and parameterized-model create/update
  paths.

### Changed
- Aligned `SimplePipeline.create` with `Pipeline` and wrapped datafit
  `plot_data`.

## [0.20.0] - 2026-07-22

### Added
- New ``planned_measurement`` client for requesting, scheduling, and
  cancelling future lab measurements, with a matching skill (#1292). A
  planned measurement requires a named protocol (``protocol_id``) and a
  cell specification.
- ``channel.watch`` to follow the live test running on a channel, and an
  org-wide ``channel.list_all`` plus channel/measurement detail helpers.
- ``cell_specification_name`` on ``LabMeasurementSummary`` so lab
  measurement listings can show the cell spec without a second lookup.
- Source tracking for material-property datasets and analyses: created
  records now carry their originating source (#1276).

### Fixed
- List endpoints now honour the filters the SDK already sends, so
  server-side narrowing is applied instead of being silently dropped
  (#1337).

## [0.19.0] - 2026-07-20

### Added
- New ``lab`` client for answering equipment / occupancy questions:
  lab status, utilization, free and stale channels, and which
  measurement is on a channel (#1262, #1274).
- ``JobClient.get_posterior_samples`` to retrieve MCMC posterior
  sample chains from Bayesian sampler datafit job metadata (#1227).
- ``validate_measurement_timing`` warning-severity checks for
  measurement ``start_time`` / ``end_time`` (future timestamps,
  end-before-start, and wall-clock vs data duration mismatch)
  (#1246).

### Changed
- Raised the ``pydantic`` dependency floor from ``>=2.12.5`` to
  ``>=2.13.4`` (#1284).

### Fixed
- Measurement artifact cache TTL is now evaluated per file, not per
  directory, so a fresh write no longer resurrects an expired sibling
  artifact (#1265).

## [0.18.0] - 2026-07-16

### Added
- New ``site``, ``cycler``, and ``channel`` clients for managing test
  equipment: create, list, retrieve, update, and delete sites, cyclers, and
  their channels. Cell measurements can now be linked to a channel via a
  ``channel_id`` field (#1163).

## [0.17.0] - 2026-07-14

### Changed
- Raw-data uploads now use a three-step signed-URL flow (``initiate-upload``
  → direct ``PUT`` to storage → ``confirm-upload``) instead of multipart
  form uploads; the file body no longer passes through the API server, so
  large raw cycler files upload more reliably. The content type is derived
  from the filename (#1204).

## [0.16.0] - 2026-07-14

### Added
- New ``analysis`` client for creating, listing, and retrieving
  measurement-scoped analyses, with results returned as dataframes (#1171).
- Organization- and project-scoped raw data storage: a new ``raw_data``
  client for uploading, listing, and downloading raw cycler files, plus
  supporting additions on ``cell_measurement`` (#1179).
- Submitted pipelines now accept ``file:`` and ``folder:`` references to
  raw data, validated client-side before submission (#1040).

## [0.15.1] - 2026-07-13

### Changed
- Internal: model enums now subclass ``StrEnum`` instead of
  ``(str, Enum)``; runtime string values are unchanged (#1174).

## [0.15.0] - 2026-07-10

### Added
- ``client.organization.usage()`` returns the organization's usage and
  limits for the current calendar-month period (resetting on the 1st).
  Simulation exposes ``.usage`` / ``.limit`` and compute adds a
  per-job-type ``.usage_by_type`` breakdown; all values are in hours and a
  ``None`` limit means that type is unconstrained (#1117).
- EIS measurement upload validation: checks the ``Z_Im`` sign convention
  and impedance magnitude consistency before upload (#1133).

## [0.14.0] - 2026-07-07

### Breaking changes
- ECM ``FitResults.capacity_Ah`` and ``ValidationResults.capacity_Ah``
  are now ``list[float]`` (one entry per measurement segment) instead of
  a single ``float``. Callers reading ``capacity_Ah`` must index the list
  (validation results are a single-element list) (#1081).

### Added
- ECM fits accept a per-measurement ``capacity`` (all-or-none across
  measurements) and a ``regularization`` smoothness prior on the R0/RC
  parameter curves; ``submit_fit_from_file`` gains a ``regularization``
  argument (#1081).

### Fixed
- Corrected the ``cell_spec.create`` example in the cell-specifications
  user guide to match the API model (#835).

## [0.13.0] - 2026-06-30

### Added
- Pipeline configs now accept ``element_type: "array_data_fit"`` for fits
  that run independently at each value of an independent variable (e.g.
  diffusivity vs. stoichiometry, parameter vs. temperature). Configurable
  with the same fields as ``data_fit``; see the new
  ``examples/pipeline/array_datafit.py`` example.
- ``client.resolve_measurement(cell_specification, cell_instance, measurement)``
  — resolve a measurement from human-readable names, walking the
  spec -> instance -> measurement hierarchy (each level filtered server-side by
  exact name). Returns the ``CellMeasurement``; raises ``IonworksError`` (404)
  when a level has no match, or (409) when a name is ambiguous within its parent.

## [0.12.0] - 2026-06-10

### Breaking changes
- ``OptimizationClient`` is decoupled from the jobs API (#861).
  Optimizations are now first-class resources under ``/optimizations``
  rather than ``/optimize``. ``get``/``wait_for_completion`` return the
  flat optimization resource (with a top-level ``status``) instead of a
  ``{"optimization": ..., "job": ...}`` dict, ``list`` returns
  ``{"optimizations": [...], "total": n}`` (no ``jobs`` key), and the
  terminal states are renamed ``completed``/``cancelled`` →
  ``succeeded``/``canceled``. Migration: read ``result["status"]`` and
  ``result["error"]`` directly, and update any status checks to the new
  state names.

### Added
- SDK support for background-job ECM parameterization (#562).
- ``JobClient.get_parameter_trace`` accessor for retrieving the
  parameter trace of a job (#815).
- Project-scoped parameterized model listing on the parameterized
  model client (#806).

### Fixed
- Corrected CC-discharge step mislabeling and an unsigned mixed-mode
  current sign error in data processing (#848).

## [0.11.0] - 2026-06-05

### Added
- New ``SimulationResult`` class (exported from the package root) — a
  typed result returned by ``SimulationClient.get_result`` exposing
  ``time_series`` and ``steps`` as DataFrames in the active backend
  (polars by default, pandas when configured) (#741).

### Fixed
- Minor ``Navigator`` documentation fix (#770).

## [0.10.3] - 2026-06-01

### Added
- New ``Navigator`` class (exported from the package root) — a cached,
  in-memory view over the cell spec → instance → measurement hierarchy
  that memoises list/fetch calls and returns name-sorted listings for
  deterministic iteration (#667).

### Changed
- Switched the ``polars`` dependency from ``polars-lts-cpu`` to the
  standard ``polars`` distribution (#768).

## [0.10.2] - 2026-05-29

### Changed
- Relaxed the ``numpy`` dependency bound to allow ``numpy>=2`` (#754).

### Removed
- Dropped unused runtime dependencies ``python-dotenv``, ``supabase``,
  and ``iwutil`` (#752).

## [0.10.1] - 2026-05-28

### Added
- ``SimplePipeline`` submission now accepts runtime options (e.g. solver
  settings) that are forwarded to the pipeline worker, giving callers
  finer-grained control over execution without changing the pipeline
  configuration (#708).

## [0.10.0] - 2026-05-27

### Added
- `MaterialClient` (`client.material`) for listing and retrieving materials.
- `MaterialPropertyDatasetClient` (`client.material_property_dataset`) for
  listing, retrieving, and downloading tabular physical property datasets
  (e.g. conductivity, diffusivity, transference number vs. concentration).
- New public models: `Material`, `MaterialPropertyDataset`, `ColumnSpec`.
- Measurement validators now cross-check the ``capacity`` and
  ``energy`` columns against the integrals of current and power,
  catching unit and sign-convention errors that previously slipped
  through (#687).
- `client.whoami()` returns the user profile that the configured API
  key resolves to — the recommended way to debug credential issues
  and confirm which organization the SDK is authorized as (#626).

### Fixed
- Measurement validators now accept parquet files alongside the
  existing supported formats (#678).
- Sign-convention vote is weighted by ``|I| * dt`` instead of row
  count, so short high-current segments are not drowned out by long
  rest periods (#682).
- The SDK no longer implicitly loads a ``.env`` file on import. Set
  ``IONWORKS_API_KEY`` in your environment (or pass it explicitly
  to ``Ionworks(...)``) before constructing the client (#630).
- JSON serialization now handles ``datetime`` and pandas
  ``Timestamp`` values in request payloads so pandas-derived
  metadata can be passed directly without manual conversion (#720).

## [0.9.0] - 2026-05-14

### Added
- `SimplePipelineClient` and `ionworks.simple_pipeline` module: a
  lightweight client for running the new SimplePipeline workflow,
  exposed on the top-level `Ionworks` client (#585).

## [0.8.0] - 2026-05-13

### Added
- `client.ecm` sub-client for ECM (Equivalent Circuit Model)
  parameterization. Supports three input modes —
  `fit_from_example` (synchronous demo), `fit_from_file` (uploads a
  local cycler file), and `fit_from_measurements` (fits measurements
  already stored in the platform). Authenticated fits run as
  background jobs; use `client.ecm.wait_for_completion(job)` to block
  until ready.
- New public models exported from `ionworks`: `EcmFitJob`,
  `FitResults`, `RcPairFit`, `SaveToProjectResponse`.
- Multi-measurement fits with per-segment SoC seeds:
  `fit_from_measurements` accepts a `measurements: list` where each
  entry can carry its own `start_step`, `end_step`, and `initial_soc`.
- Capacity co-fitting from an OCV(SoC) curve: `ecm_options` now accepts
  `ocv_soc_curve: {soc, ocv}` and optional `bounds_capacity: {lo, hi}`;
  the SDK validates curve monotonicity, SoC range, length parity, and
  bounds inversion locally before the request hits the wire.
- Knot-resolution controls on `ecm_options`: `num_knots`, `num_knots_r0`,
  `knot_schedule`, `clamp_boundary_knots`, `clamp_max_ratio`.
- `Ionworks.post_multipart` helper on the HTTP client for endpoints
  that accept optional multipart file uploads alongside query params.
- `JobClient.get_metadata(job_id)` — fetch the parsed JSON contents of a
  job's metadata blob (large fields stripped from `job.result` and
  persisted to storage, e.g. `validation_results` and
  `validation_plot_config`). API-key clients couldn't previously reach
  these fields because the path through Supabase signed URLs requires a
  Supabase JWT (#515).
- `PipelineClient.get_element_metadata(pipeline_id, element_name,
  elements=None)` — convenience wrapper that resolves an element by name
  to its job and returns the metadata blob. Pass a pre-fetched
  `elements` list to avoid re-fetching when pulling metadata for several
  elements of the same pipeline (#515).

## [0.7.0] - 2026-05-11

### Added
- `/protocols/convert` endpoint and client helper to convert a UCP
  protocol into vendor-specific cycler protocol files (#578).
- Auto-detect `IONWORKS_PROJECT_ID` from the environment so callers
  don't have to pass it explicitly (#536).

### Changed
- Validation errors now carry a structured `ValidationIssue` list
  with stable `CheckName` enum, `severity`, `message`, and
  `payload`. Callers should branch on `e.has_check(CheckName.X)`
  instead of substring-matching error messages (#544).

## [0.6.0] - 2026-05-04

### Added
- `skip_checks` parameter on validation entry points so callers with
  least-privileged API keys can opt out of project/permission checks
  while keeping schema validation (#534).
- Helpers on the client for uploading and managing custom PyBaMM
  models (#566).

### Changed
- UCP input is now backed by a machine-checkable JSON Schema, with
  parser parity tests ensuring the Python parser and schema agree
  (#494).
