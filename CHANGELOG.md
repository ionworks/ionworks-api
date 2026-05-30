# Changelog — ionworks-api

All notable changes to this package are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package follows [Semantic Versioning](https://semver.org/).

For platform-wide release notes (Studio, pipeline, SDK, and more),
see [docs.ionworks.com/changelog](https://docs.ionworks.com/changelog).

<!-- New release sections are prepended below by the release-packages skill. -->

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
