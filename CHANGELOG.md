# Changelog — ionworks-api

All notable changes to this package are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package follows [Semantic Versioning](https://semver.org/).

For platform-wide release notes (Studio, pipeline, SDK, and more),
see [docs.ionworks.com/changelog](https://docs.ionworks.com/changelog).

<!-- New release sections are prepended below by the release-packages skill. -->

## [0.28.0] - 2026-08-18

### Added
- `client.channel.update()` accepts a `service_scheduled_until` datetime,
  recording when a booked outage is expected to end (must be after
  `service_scheduled_at`). `client.channel.list_incidents()` reads back a
  channel's out-of-service history, newest first, including whether the
  current outage is overdue to return.
- `client.pipeline` gains `update()`, `cancel()`, and `delete()`.
- `client.protocol.convert()` accepts `verify=True` to ask the server to
  round-trip the emitted file through its parser and reject the conversion if
  the protocol no longer means the same thing (a dropped loop count, goto, or
  safety bound). Reliable today for `biologic_bttest` and `novonix`; the other
  writers still report benign artifacts as differences.
- New validator `validate_charge_discharge_column_direction` detects a cycler
  export with the charge/discharge capacity (or energy) column labels
  inverted, which is otherwise invisible to every cumulative-reset and
  magnitude check.
- `client.protocol.convert()` accepts `"arbin_sdx"` as a `target`, writing the
  Arbin MITS Pro 8+ `.sdx` dialect alongside the existing MITS Pro ≤7 `.sdu`
  writer (`"arbin"`, unchanged and not deprecated).

### Fixed
- `client.cell_specification.list(name_exact=...)` (and the internal lookup
  `create_or_get` falls back to) is now project-scoped, matching spec names
  being unique per project rather than per organization — an org-wide exact
  match could previously return a different project's spec or misreport a
  locally-unique name as ambiguous.
- `validate_charge_discharge_column_direction`'s same-sign-current check no
  longer discards a step's vote when a single boundary sample inherited from
  the preceding step pollutes its min/max.

## [0.27.0] - 2026-08-11

### Breaking changes
- `client.material.list()` is now project-scoped: it returns only the materials
  in one project, and raises `ValueError` when no `project_id` is given and the
  client has no default. Previously it listed materials across the whole
  organization, and `project_id` only scoped each material's `property_count`.
  Pass `project_id=` explicitly, or set a client-wide default (via
  `IONWORKS_PROJECT_ID`), and expect a narrower result set than before.

### Added
- Write methods on `client.material_property_dataset`: `create()` uploads a
  DataFrame (serialised to parquet, inferring `columns` from a trailing
  `name [unit]` header when not given), `replace_file()` swaps the stored file,
  `update()` patches metadata, and `delete()` removes a dataset.
  `get_download_url(kind="parquet"|"original")` returns a signed URL.

  `replace_file()` does not infer columns, so it cannot erase stored units: when
  `columns` is omitted it checks that the replacement's layout matches the
  stored specs and raises `ValueError` on a mismatch rather than mislabelling
  the data.
- Server-side filtering, ordering, and pagination on `client.material.list()`:
  `name`, `manufacturer`, `product_id`, `created_at` / `updated_at` (plus
  `_gt` / `_lt` range bounds), `order_by`, and `order`. Text filters take a
  bare value for an exact match or an operator prefix such as
  `"ilike.%graphite%"` for a partial one; `name_exact=` is shorthand for exact
  equality and cannot be combined with `name`.
- An `ionworks` console script for managing the Agentic Toolkit:
  `ionworks skills install` (aliased as `update`) downloads the toolkit and
  installs it for your agents, removing a previous install so retired skills do
  not linger, and `ionworks skills list` shows what is installed. Both take
  `--global` to target the user-level directory instead of the current project,
  and `--agent` to name target agents explicitly instead of auto-detecting.

## [0.26.0] - 2026-08-10

### Added
- Material reverse lookup on `cell_spec.list(...)`: `material_id` returns the
  specs referencing a material through any component slot, and accepts a list to
  match any of several materials. Per-slot `anode_material_id`,
  `cathode_material_id`, `electrolyte_material_id`, `separator_material_id`, and
  `case_material_id` restrict the search to one slot, and `exclude_cell_spec_id`
  drops a spec from the results. A reverse lookup is project-scoped, so it needs
  a `project_id` (falling back to the client's, or `IONWORKS_PROJECT_ID`).
  Filtering happens in the database, so `total` counts every matching spec rather
  than the rows in the page.
- `cell_spec.related_specs(cell_spec_id, ...)` returns the other specs that share
  a component material with a given spec. By default any of its slot materials
  matches in any slot; pass `slots=["anode", "cathode"]` to compare each named
  slot only against the same slot on the source spec. `exclude_self` (default
  True) and `include_components` control self-exclusion and whether nested
  component and material data comes back.
- `client.planned_measurements.auto_schedule_proposal(...)` proposes the earliest
  channel reservations for a list of requested tests, in priority order. The
  search is unbounded in time, so each request gets the earliest start that
  actually fits; an optional `start_after` defers the whole batch when an
  operator cannot begin immediately. The result is transient — nothing is
  reserved until it is applied.
- `client.planned_measurements.apply_auto_schedule_proposal(...)` commits the
  assignments you selected from a reviewed proposal. The API re-checks each one
  against its proposal-time version and current channel availability, and on a
  stale or conflicting proposal rolls back the whole batch and raises
  `IonworksError` with `error_code == "SCHEDULE_STALE"` — generate a fresh
  proposal and retry.
- `AutoScheduleProposal` and `AutoScheduleAssignment` models, exported from the
  top-level `ionworks` namespace. `AutoScheduleAssignment.is_scheduled` tells you
  whether an assignment is complete and safe to apply; incomplete ones carry an
  `unscheduled_reason` explaining that no channel in the project can take the
  test.

## [0.25.0] - 2026-08-06

### Added
- `client.search.query(...)` runs one query across every entity type in the
  organization — projects, models, cell specifications and instances,
  measurements, parameterized models, templates, materials, studies,
  optimizations, and pipelines — returning ranked `SearchResult` records in a
  `SearchResponse`. Name fields match on case-insensitive substring and
  free-form text on prefix full-text search, with substring matches ranked
  first. `per_type` keeps one entity type from crowding out the rest, and
  `entity_types` / `project_id` narrow the sweep; search spans the whole
  organization by default rather than the client's `project_id`.
- `client.cycler` tracks hardware service: `list_service_events`,
  `start_service`, and `complete_service`, plus the `CyclerServiceEvent` model.
  Starting service opens an event and an incident on every channel so the whole
  instrument reads as down; channels already out of service keep their own more
  specific incident and stay out when the service completes.
- `client.protocol` now covers the whole protocol lifecycle rather than
  validation and conversion alone:
  - **Saved protocols** — `list`, `get`, `find_by_name`, `create`,
    `create_or_get`, `update`, `delete`, plus `human_readable` and
    `source_protocol` renderings. Protocols are project-scoped and
    content-addressed, so saving one that already exists returns the existing
    record instead of a duplicate.
  - **`parse_file`** — turn a vendor cycler protocol file (Maccor, Arbin,
    Neware, Novonix, BioLogic, …) into UCP, reporting any drive cycles or
    subroutines the file referenced but did not carry.
- New models: `Protocol` and `ParsedProtocol`, exported from the package root
  (`from ionworks import Protocol, ParsedProtocol`).
- `protocol.list` gained `name` / `name_exact` / `created_by_email` /
  `created_after` / `created_before` / `updated_after` / `updated_before` /
  `order_by` / `order`, matching the other list clients. Filtering, ordering
  and pagination are applied by the database, so `total` counts every
  matching protocol rather than the rows in the page.
- `protocol.find_by_name` now matches server-side. It previously scanned an
  unpaginated listing, so it returned `None` for any protocol past the
  server's row cap. It also raises `ValueError` when several protocols in the
  project share the name — protocols are deduplicated on their body, not their
  name, so returning an arbitrary match could silently simulate the wrong one.

### Changed
- Protocol responses no longer carry `access_level`. Every protocol reachable
  through the API is project-scoped, so the field could only ever read
  `"project"`.

### Fixed
- `simulation.protocol` now sends the resolved `project_id` to the batch
  endpoint, so a run that builds a quick model succeeds instead of being
  rejected for having no project to own the parameterized model. The
  `project_id` is resolved as before — from the config, the client, or
  `IONWORKS_PROJECT_ID` — so no call site has to change.

## [0.24.1] - 2026-08-05

### Fixed
- `jobs.get_posterior_samples` documented a narrower contract than it has. Every
  sampler populates the sample keys, not only Bayesian ones; it is fits driven by
  a conventional optimizer (`CMAES`, `ScipyMinimize`), along with optimization and
  validation jobs, that return an empty dict. The docstring now also states that
  `samples` values are nested `(starts, iterations)` for a multistart fit and a
  flat per-iteration list for a single start — so index the iteration axis last
  (`[..., burnin:]`) to handle both — that `sample_costs` is shaped like one
  parameter's chain, and that `sample_burnin` is `None` for samplers with no
  burn-in concept (`GridSearch`, `PointEstimateSampler`). Behaviour is unchanged.

## [0.24.0] - 2026-08-03

### Added
- `protocols.convert` accepts `nominal_capacity_ah`, the rated cell capacity in
  amp-hours. It is required for the `neware` target when the protocol uses
  C-rate steps or cutoffs, because Neware sets current in absolute mA and has
  no C-rate mode. Other targets express C-rate natively and ignore it.

### Changed
- Raised the `requests` lower bound to `>=2.34.2`.

### Fixed
- `jobs.get_plot_data` now calls `GET /pipelines/datafits/{job_id}/plot_data`.
  The previous unprefixed path returned a 404, so fetching model-vs-data plot
  traces for a data-fit job failed.

## [0.23.0] - 2026-07-29

### Added
- `ProtocolSimulationBatchRequest` accepts a `project_id`. Parsed protocols are
  created as project-scoped experiment templates, so the backend now requires
  the project. When omitted, it falls back to the `project_id` configured on
  the `Ionworks` client (or the `IONWORKS_PROJECT_ID` environment variable).

## [0.22.0] - 2026-07-28

### Breaking changes
- `channel.create` no longer accepts `out_of_commission` and rejects it with
  `BAD_REQUEST`: a channel is always commissioned in service. Use
  `channel.update` to take one out of service afterwards, which records the
  outage in the channel's incident history.

### Added
- Channel outage history. Passing `out_of_commission` to `channel.update` now
  records an incident, and may be accompanied by `incident_category` (one of
  `hardware_failure`, `maintenance`, `calibration`, `decommissioned`, `other`;
  defaults to `other`) and `incident_notes` when taking a channel out of
  service, or `resolution_notes` when returning it. Re-sending the value a
  channel already has is a no-op rather than a new outage.
- `MeasurementProcessingError`, raised when the server cannot process an
  uploaded `time_series` measurement. Its `failures` attribute maps each
  affected measurement id to the reason, so a batch can report several at
  once.
- `cell_measurement.wait_for_processing`, plus a `wait_for_processing`
  argument (default `True`) on the upload methods, so an upload no longer
  returns a measurement that looks created but holds no usable steps.

### Changed
- `channel.update` now also raises `CONFLICT` when a channel's service state
  was changed concurrently; re-read the channel and retry.

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
