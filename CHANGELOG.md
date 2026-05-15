# Changelog — ionworks-api

All notable changes to this package are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package follows [Semantic Versioning](https://semver.org/).

For platform-wide release notes (Studio, pipeline, SDK, and more),
see [docs.ionworks.com/changelog](https://docs.ionworks.com/changelog).

<!-- New release sections are prepended below by the release-packages skill. -->

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
