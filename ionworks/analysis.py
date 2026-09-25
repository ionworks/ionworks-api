"""Analysis client for feature-extraction results derived from a measurement.

This module provides :class:`AnalysisClient` for creating and reading
*analyses* — features extracted from a single cell measurement (e.g. ECM
parameters fit from EIS, or LLI/LAM degradation points from RPT data). Each
analysis stores a loosely-structured parquet table plus light metadata, keyed
to one ``cell_measurement``.
"""

from __future__ import annotations

import json
from typing import Any

from ._dataframe import parquet_file_part, parquet_to_dataframe, to_polars
from .errors import IonworksError
from .models import (
    Analysis,
    PaginatedList,
    _build_endpoint,
    _parse_list_response,
    collect_all_pages,
)
from .validators import DataFrame

#: The list route caps ``limit`` at 100, so searching a measurement that carries
#: more analyses than that has to page.
_PAGE_SIZE = 100

#: Analyses quoted in a "nothing matched" message. A measurement accumulates one
#: auto-generated analysis per section, so the full list can be long.
_MAX_DESCRIBED = 10


def _describe_analyses(records: list[Analysis]) -> str:
    """Render analyses for an error message, so the right selector is copyable."""
    if not records:
        return "none"
    described = "; ".join(
        f"{record.id} name={record.name!r} analysis_type={record.analysis_type!r}"
        for record in records[:_MAX_DESCRIBED]
    )
    if len(records) > _MAX_DESCRIBED:
        described += f"; ... and {len(records) - _MAX_DESCRIBED} more"
    return described


class AnalysisClient:
    """Client for analyses derived from a single cell measurement.

    Access via ``client.analysis``.
    """

    _BASE = "/analyses"

    def __init__(self, client: Any) -> None:
        """Initialise the AnalysisClient.

        Parameters
        ----------
        client : Any
            The parent :class:`~ionworks.client.Ionworks` instance.
        """
        self.client = client

    def create(
        self,
        measurement_id: str,
        name: str,
        analysis_type: str,
        data: DataFrame | dict,
        *,
        columns: list[dict] | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> Analysis:
        """Create an analysis and upload its extracted-feature table.

        The ``data`` table is serialised to parquet client-side and uploaded in
        a single multipart request.

        Parameters
        ----------
        measurement_id : str
            ID of the parent cell measurement this analysis is derived from.
        name : str
            User-provided analysis name.
        analysis_type : str
            Kind of analysis, e.g. ``"ecm_from_eis"``. Free-form; see
            ``ionworks.AnalysisType`` for the advisory set of well-known values
            (a ``StrEnum`` whose members can be passed directly here).
        data : DataFrame | dict
            The extracted features as a polars/pandas DataFrame (or a
            column-name -> values dict). Serialised to parquet automatically.
        columns : list[dict] | None, optional
            Column specs describing the parquet for header preview, e.g.
            ``[{"name": "lam_ne", "unit": "%", "dtype": "float"}]``. When
            omitted, no column metadata is sent.
        metadata : dict | None, optional
            Loose metadata (source RPT/cycle identity, extractor parameters,
            etc.). Defaults to an empty object.
        notes : str | None, optional
            Free-text description of how the analysis was performed.

        Returns
        -------
        Analysis
            The created analysis record.
        """
        form: dict[str, str] = {
            "measurement_id": measurement_id,
            "name": name,
            "analysis_type": analysis_type,
            "columns": json.dumps(columns if columns is not None else []),
            "metadata": json.dumps(metadata if metadata is not None else {}),
        }
        if notes is not None:
            form["notes"] = notes
        response = self.client.upload_multipart(
            self._BASE, data=form, files=parquet_file_part(to_polars(data))
        )
        return Analysis(**response)

    def get(self, analysis_id: str) -> Analysis:
        """Retrieve an analysis record by ID.

        Parameters
        ----------
        analysis_id : str
            The analysis UUID.

        Returns
        -------
        Analysis
            The analysis record.
        """
        return Analysis(**self.client.get(f"{self._BASE}/{analysis_id}"))

    def list(
        self,
        measurement_id: str | None = None,
        *,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> PaginatedList[Analysis]:
        """List analyses for a measurement or a project, newest first.

        Provide **exactly one** of ``measurement_id`` or ``project_id``:
        ``measurement_id`` returns the analyses derived from that single
        measurement; ``project_id`` returns all analyses across the project.

        Parameters
        ----------
        measurement_id : str | None, optional
            ID of the parent cell measurement to list analyses for.
        project_id : str | None, optional
            ID of the project to list all analyses for. Keyword-only.
        limit : int | None, optional
            Maximum number of records to return (server default: 100).
        offset : int | None, optional
            Number of records to skip for pagination (default: 0).

        Returns
        -------
        PaginatedList[Analysis]
            Paginated list of analysis records.

        Raises
        ------
        ValueError
            If neither or both of ``measurement_id`` / ``project_id`` are given.
        """
        if (measurement_id is None) == (project_id is None):
            raise ValueError("Provide exactly one of measurement_id or project_id.")
        params: dict[str, Any] = {}
        if measurement_id is not None:
            params["measurement_id"] = measurement_id
        else:
            params["project_id"] = project_id
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        endpoint = _build_endpoint(self._BASE, params)
        return _parse_list_response(self.client.get(endpoint), Analysis)

    def _all_for_measurement(self, measurement_id: str) -> list[Analysis]:
        """Every analysis on a measurement, paging past the route's limit."""
        return collect_all_pages(
            lambda limit, offset: self.list(
                measurement_id=measurement_id, limit=limit, offset=offset
            ),
            _PAGE_SIZE,
        )

    def find_one(
        self,
        measurement_id: str,
        *,
        analysis_id: str | None = None,
        analysis_type: str | None = None,
        name: str | None = None,
    ) -> Analysis:
        """The single analysis on a measurement matching every field given.

        Fields are combined with AND, so two analyses sharing a type can be
        separated by name. Nothing in the database prevents two hand-uploaded
        analyses sharing a name and type on one measurement, so an ambiguous
        selector raises rather than resolving to whichever was created last.

        ``analysis_id`` is the exception: it identifies the row on its own and
        any other fields given are ignored, so renaming or retyping an analysis
        cannot invalidate a selector that names its id.

        Parameters
        ----------
        measurement_id : str
            Measurement whose analyses are searched.
        analysis_id : str | None, optional
            Match this analysis id exactly.
        analysis_type : str | None, optional
            Match this ``analysis_type``, e.g. ``AnalysisType.LAM_LLI_FROM_RPT``.
        name : str | None, optional
            Match this name exactly.

        Returns
        -------
        Analysis
            The one matching analysis record.

        Raises
        ------
        ValueError
            If no selector field is given, which would match everything.
        IonworksError
            If no analysis matches, or more than one does.
        """
        # (keyword the caller passed, attribute it matches on, value).
        wanted = [
            ("analysis_id", "id", analysis_id),
            ("analysis_type", "analysis_type", analysis_type),
            ("name", "name", name),
        ]
        if all(value is None for _, _, value in wanted):
            raise ValueError(
                "find_one needs at least one of analysis_id, analysis_type or "
                "name; an empty selector matches every analysis on the "
                "measurement."
            )
        if analysis_id is not None:
            # Used alone, so a later rename cannot reject the pin naming it.
            found = self.get(analysis_id)
            if found.measurement_id != measurement_id:
                raise IonworksError(
                    f"Analysis {analysis_id} belongs to measurement "
                    f"{found.measurement_id}, not {measurement_id}."
                )
            return found
        candidates = self._all_for_measurement(measurement_id)
        matches = [
            candidate
            for candidate in candidates
            if all(
                value is None or getattr(candidate, attr) == value
                for _, attr, value in wanted
            )
        ]
        selector = ", ".join(
            f"{keyword}={value!r}" for keyword, _, value in wanted if value is not None
        )
        if not matches:
            raise IonworksError(
                f"No analysis on measurement {measurement_id} matches "
                f"{selector}. Analyses on this measurement: "
                f"{_describe_analyses(candidates)}."
            )
        if len(matches) > 1:
            raise IonworksError(
                f"{len(matches)} analyses on measurement {measurement_id} match "
                f"{selector}: {_describe_analyses(matches)}. Narrow the "
                "selector, or give analysis_id to name one exactly."
            )
        return matches[0]

    def update(self, analysis_id: str, data: dict) -> Analysis:
        """Update an analysis's metadata fields (does not replace the parquet).

        Parameters
        ----------
        analysis_id : str
            The analysis UUID.
        data : dict
            Partial update. Any of ``name``, ``analysis_type``, ``columns``,
            ``metadata``, ``notes``.

        Returns
        -------
        Analysis
            The updated analysis record.
        """
        return Analysis(**self.client.patch(f"{self._BASE}/{analysis_id}", data))

    def delete(self, analysis_id: str) -> None:
        """Delete an analysis record and its parquet file.

        Parameters
        ----------
        analysis_id : str
            The analysis UUID.
        """
        self.client.delete(f"{self._BASE}/{analysis_id}")

    def get_download_url(self, analysis_id: str) -> str:
        """Return a short-lived signed URL for the analysis parquet.

        Parameters
        ----------
        analysis_id : str
            The analysis UUID.

        Returns
        -------
        str
            A signed URL that can be used to download the parquet directly.
        """
        return self.client.get(f"{self._BASE}/{analysis_id}/download-url")["url"]

    def get_data(self, analysis_id: str) -> DataFrame:
        """Download the analysis parquet and return it as a DataFrame.

        Fetches a signed URL and reads the parquet directly from storage.

        Parameters
        ----------
        analysis_id : str
            The analysis UUID.

        Returns
        -------
        DataFrame
            Polars or pandas DataFrame (per the active backend) with the
            extracted-feature columns.
        """
        url = self.get_download_url(analysis_id)
        return parquet_to_dataframe(url)
