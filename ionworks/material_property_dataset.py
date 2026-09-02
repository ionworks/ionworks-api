"""Material property client for retrieving tabular property datasets.

This module provides :class:`MaterialPropertyDatasetClient` for reading material property
datasets (e.g. conductivity, diffusivity, and transference number as a function of
electrolyte concentration) stored in the Ionworks backend.
"""

from __future__ import annotations

import json
import re
from typing import Any

import polars as pl

from ._dataframe import parquet_file_part, to_polars
from .models import (
    ColumnSpec,
    MaterialPropertyDataset,
    PaginatedList,
    _build_endpoint,
    _parse_list_response,
)
from .validators import DataFrame, get_dataframe_backend

#: Trailing ``name [unit]`` pattern used to split a column header into a display
#: name and a physical unit, e.g. ``"kappa [S/m]"`` -> ``("kappa", "S/m")``.
_UNIT_SUFFIX = re.compile(r"\s*\[([^\]]+)\]\s*$")


def _infer_column_specs(df: pl.DataFrame) -> list[ColumnSpec]:
    """Build column specs from a DataFrame's columns, parsing ``name [unit]``.

    Each column becomes a :class:`~ionworks.models.ColumnSpec` whose
    ``source_column_index`` is the column's position. A trailing ``[unit]`` in
    the column name is split out into the ``unit`` field; if absent, the unit is
    an empty string.

    Parameters
    ----------
    df : pl.DataFrame
        DataFrame whose columns describe the property dataset.

    Returns
    -------
    list[ColumnSpec]
        One spec per column, in column order.
    """
    specs = []
    for idx, col in enumerate(df.columns):
        match = _UNIT_SUFFIX.search(col)
        name = col[: match.start()] if match else col
        unit = match.group(1) if match else ""
        specs.append(ColumnSpec(name=name, unit=unit, source_column_index=idx))
    return specs


def _columns_to_dicts(
    columns: list[ColumnSpec] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalise column specs to plain dicts.

    Parameters
    ----------
    columns : list[ColumnSpec] | list[dict[str, Any]]
        Column specs as :class:`~ionworks.models.ColumnSpec` objects or plain
        dicts.

    Returns
    -------
    list[dict[str, Any]]
        Each spec as a plain dict (``ColumnSpec`` objects via ``model_dump``).
    """
    return [c.model_dump() if isinstance(c, ColumnSpec) else c for c in columns]


def _check_layout_matches(df: pl.DataFrame, stored: list[ColumnSpec]) -> None:
    """Verify a replacement DataFrame matches stored column specs positionally.

    When ``columns`` is omitted on a file replacement, the backend reuses the
    stored ``source_column_index`` values, selecting replacement columns by
    position. This checks each stored position exists in ``df`` and still holds
    a column of the same name, so preserved specs cannot silently attach to the
    wrong data.

    Parameters
    ----------
    df : pl.DataFrame
        The replacement data.
    stored : list[ColumnSpec]
        The dataset's existing column specs.

    Raises
    ------
    ValueError
        If any stored position is out of range for ``df`` or maps to a
        differently named column.
    """
    n = len(df.columns)
    mismatched = [
        s
        for s in stored
        if s.source_column_index >= n or df.columns[s.source_column_index] != s.name
    ]
    if mismatched:
        names = ", ".join(
            f"{s.name!r} (position {s.source_column_index})" for s in mismatched
        )
        raise ValueError(
            "replacement data's column layout differs from the stored "
            f"specs for: {names}. The stored specs select columns by "
            "position, so this would mislabel data. Pass explicit "
            "`columns` to reshape the dataset deliberately."
        )


class MaterialPropertyDatasetClient:
    """Client for reading material property datasets.

    Access via ``client.material_property_dataset``.
    """

    _BASE = "/material_property_datasets"

    def __init__(self, client: Any) -> None:
        """Initialise the MaterialPropertyDatasetClient.

        Parameters
        ----------
        client : Any
            The parent :class:`~ionworks.client.Ionworks` instance.
        """
        self.client = client

    def list(
        self,
        material_id: str,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> PaginatedList[MaterialPropertyDataset]:
        """List material property datasets for a given material.

        Parameters
        ----------
        material_id : str
            ID of the material to list datasets for.
        project_id : str | None, optional
            When provided, restricts results to datasets in this project.
        limit : int | None, optional
            Maximum number of records to return (server default: 100).
        offset : int | None, optional
            Number of records to skip for pagination (default: 0).

        Returns
        -------
        PaginatedList[MaterialPropertyDataset]
            Paginated list of material property dataset records.
        """
        params: dict[str, Any] = {"material_id": material_id}
        if project_id is not None:
            params["project_id"] = project_id
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        endpoint = _build_endpoint(self._BASE, params)
        return _parse_list_response(self.client.get(endpoint), MaterialPropertyDataset)

    def get(self, dataset_id: str) -> MaterialPropertyDataset:
        """Retrieve a material property dataset record by ID.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.

        Returns
        -------
        MaterialPropertyDataset
            The material property dataset record.
        """
        return MaterialPropertyDataset(**self.client.get(f"{self._BASE}/{dataset_id}"))

    def get_units(self, dataset_id: str) -> dict[str, str]:
        """Return a mapping of column name to unit for a property dataset.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.

        Returns
        -------
        dict[str, str]
            Mapping of column name to its physical unit, e.g.
            ``{"c_e": "mol/L", "kappa": "S/m"}``.
        """
        record = self.get(dataset_id)
        return {col.name: col.unit for col in record.columns}

    def get_data(self, dataset_id: str) -> DataFrame:
        """Download and return the property dataset as a DataFrame.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.

        Returns
        -------
        DataFrame
            Polars or pandas DataFrame depending on the active backend,
            containing all columns declared for this dataset.
        """
        col_data = self.client.get(f"{self._BASE}/{dataset_id}/data")
        df = pl.DataFrame(col_data)
        if get_dataframe_backend() == "pandas":
            return df.to_pandas()
        return df

    def create(
        self,
        material_id: str,
        name: str,
        data: DataFrame | dict[str, Any],
        *,
        columns: list[ColumnSpec] | list[dict[str, Any]] | None = None,
        project_id: str | None = None,
    ) -> MaterialPropertyDataset:
        """Create a material property dataset from tabular data.

        The DataFrame is serialised to parquet and uploaded; the backend stores
        both the processed parquet and the original file. All values are coerced
        to Float64, with non-parseable cells stored as NaN.

        Parameters
        ----------
        material_id : str
            ID of the material this dataset describes.
        name : str
            User-facing name for the dataset.
        data : DataFrame | dict[str, Any]
            The property data as a polars/pandas DataFrame or a
            column-name -> values dict. Column order is preserved and used as
            the positional index for each column spec.
        columns : list[ColumnSpec] | list[dict[str, Any]] | None, optional
            Column specifications declaring the display name and unit of each
            column. When omitted, specs are inferred from ``data`` by parsing a
            trailing ``[unit]`` from each column name (e.g. ``"kappa [S/m]"``).
            Pass explicit specs to control units precisely.
        project_id : str | None, optional
            Project to create the dataset in. Falls back to the client's default
            ``project_id`` when omitted.

        Returns
        -------
        MaterialPropertyDataset
            The created dataset record.

        Raises
        ------
        ValueError
            If no ``project_id`` is provided and the client has no default.
        """
        project_id = project_id or self.client.project_id
        if not project_id:
            raise ValueError(
                "project_id is required or IONWORKS_PROJECT_ID must be set"
            )

        df = to_polars(data)
        specs = columns if columns is not None else _infer_column_specs(df)
        form = {
            "material_id": material_id,
            "project_id": project_id,
            "name": name,
            "columns": json.dumps(_columns_to_dicts(specs)),
        }
        response = self.client.upload_multipart(
            self._BASE, data=form, files=parquet_file_part(df)
        )
        return MaterialPropertyDataset(**response)

    def update(
        self,
        dataset_id: str,
        *,
        name: str | None = None,
        columns: list[ColumnSpec] | list[dict[str, Any]] | None = None,
    ) -> MaterialPropertyDataset:
        """Update a dataset's metadata (name and/or column specs).

        This is a metadata-only update; it does not change the stored data
        file. To replace the underlying data, use :meth:`replace_file`. When
        ``columns`` is provided, the backend re-parses the original uploaded
        file and rebuilds the processed parquet with the new column metadata,
        so the column names must still match those in the original file.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.
        name : str | None, optional
            New name. Left unchanged when omitted.
        columns : list[ColumnSpec] | list[dict[str, Any]] | None, optional
            New column specifications. Left unchanged when omitted.

        Returns
        -------
        MaterialPropertyDataset
            The updated dataset record.
        """
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if columns is not None:
            payload["columns"] = _columns_to_dicts(columns)
        response = self.client.patch(f"{self._BASE}/{dataset_id}", payload)
        return MaterialPropertyDataset(**response)

    def replace_file(
        self,
        dataset_id: str,
        data: DataFrame | dict[str, Any],
        *,
        name: str | None = None,
        columns: list[ColumnSpec] | list[dict[str, Any]] | None = None,
    ) -> MaterialPropertyDataset:
        """Replace the stored data file for a dataset, keeping its ID.

        The DataFrame is serialised to parquet and replaces both the processed
        parquet and the original file. The dataset record keeps its ID and all
        other metadata. ``name`` and ``columns`` may optionally be updated in
        the same request; when omitted the existing values are preserved.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.
        data : DataFrame | dict[str, Any]
            The replacement data as a polars/pandas DataFrame or a
            column-name -> values dict.
        name : str | None, optional
            New name. Left unchanged when omitted.
        columns : list[ColumnSpec] | list[dict[str, Any]] | None, optional
            New column specifications. When omitted, the dataset's existing
            column specs (names and units) are preserved — pass explicit specs
            only to change them. Unlike :meth:`create`, specs are **not**
            inferred from ``data`` here, so replacing a file with plain headers
            never silently erases stored units.

        Returns
        -------
        MaterialPropertyDataset
            The updated dataset record.

        Raises
        ------
        ValueError
            If ``columns`` is omitted and the replacement data's column layout
            differs from the stored specs. The stored specs select columns by
            position, so a reordered or resized replacement would silently
            mislabel data; pass explicit ``columns`` to reshape deliberately.
        """
        df = to_polars(data)
        form: dict[str, Any] = {}
        if columns is not None:
            form["columns"] = json.dumps(_columns_to_dicts(columns))
        else:
            _check_layout_matches(df, self.get(dataset_id).columns)
        if name is not None:
            form["name"] = name
        response = self.client.upload_multipart(
            f"{self._BASE}/{dataset_id}/file",
            data=form,
            files=parquet_file_part(df),
            method="PATCH",
        )
        return MaterialPropertyDataset(**response)

    def get_download_url(self, dataset_id: str, kind: str = "parquet") -> str:
        """Return a short-lived signed download URL for a dataset file.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.
        kind : str, optional
            Which file to link: ``"parquet"`` (default) for the processed
            dataset, or ``"original"`` for the raw uploaded file.

        Returns
        -------
        str
            A signed URL valid for a few minutes.
        """
        endpoint = _build_endpoint(
            f"{self._BASE}/{dataset_id}/download-url", {"kind": kind}
        )
        return self.client.get(endpoint)["url"]

    def delete(self, dataset_id: str) -> None:
        """Delete a material property dataset record and its storage files.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.
        """
        self.client.delete(f"{self._BASE}/{dataset_id}")
