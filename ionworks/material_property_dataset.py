"""Material property client for retrieving tabular property datasets.

This module provides :class:`MaterialPropertyDatasetClient` for reading material property
datasets (e.g. conductivity, diffusivity, and transference number as a function of
electrolyte concentration) stored in the Ionworks backend.
"""

from __future__ import annotations

import json
import math
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

    def to_prior(
        self,
        dataset_id: str,
        column: str,
        parameter_name: str,
        *,
        std: float | None = None,
        rel_std: float | None = None,
        distribution: str = "normal",
        regularizer_weight: float | None = None,
        row: int | None = None,
    ) -> Any:
        """Build a fit prior from a scalar value stored in a property dataset.

        Parameters
        ----------
        dataset_id : str
            UUID of the material property dataset holding the scalar.
        column : str
            Name of the column holding the value, as it appears in the
            dataset's column specs (see :meth:`get_units`).
        parameter_name : str
            Name of the fit parameter the prior applies to, e.g.
            ``"Negative particle radius [m]"``. This is the parameter name used
            by the pipeline, not the dataset's column name.
        std : float | None, optional
            Absolute standard deviation of the prior. Must be positive and
            finite. A scalar carries no spread of its own, so exactly one of
            ``std`` or ``rel_std`` is required. For
            ``distribution="lognormal"`` this is the standard deviation of
            ``log(X)``, not of the value itself.
        rel_std : float | None, optional
            Standard deviation as a fraction of the value, so ``rel_std=0.1``
            means 10%. For ``"normal"`` this gives ``std = rel_std * abs(value)``;
            for ``"lognormal"`` it is used directly as the standard deviation of
            ``log(X)``, where a fraction is already the natural scale.
        distribution : str, optional
            ``"normal"`` (default) or ``"lognormal"``. Prefer ``"lognormal"``
            for strictly positive parameters spanning orders of magnitude, such
            as diffusivities and conductivities. The lognormal is centred on
            ``log(value)``, matching how ``mean`` is defined everywhere else in
            the stack (see Notes).
        regularizer_weight : float | None, optional
            Weight applied to the prior's contribution to the fit cost.
            Defaults to 1.0 when unset.
        row : int | None, optional
            Row index to read when the dataset holds more than one row. By
            default the dataset must have exactly one row, so a curve cannot be
            silently reduced to its first point.

        Returns
        -------
        ionworks_schema.priors.Prior
            A prior on ``parameter_name`` centred on the stored value, ready to
            pass in a ``DataFit``'s ``priors`` mapping.

        Raises
        ------
        ValueError
            If neither or both of ``std`` and ``rel_std`` are given, or the
            resulting width is not positive and finite; if ``distribution`` is
            not ``"normal"`` or ``"lognormal"``; if ``column`` is not in the
            dataset; if the dataset has more than one row and no ``row`` was
            given, or ``row`` is out of range; if the value is missing,
            non-numeric or non-finite; or if it is non-positive for
            ``"lognormal"``.

        Notes
        -----
        A lognormal's ``mean`` is the log-mean, set to ``log(value)`` as
        everywhere else in the stack. Its fit penalty is minimised at the mode,
        ``exp(mean - std**2)``, so a wide lognormal prior pulls the fit below
        the stored value (~22% at ``rel_std=0.5``). That is left uncorrected
        because ``ppf``/``rand`` and the SOBER sampler read ``mean`` as the
        log-mean, so an offset would de-centre sampling by the same factor.
        Prefer a smaller ``rel_std`` or ``"normal"`` when the bias matters.

        Examples
        --------
        >>> from ionworks import Ionworks
        >>> import ionworks_schema as iws
        >>> client = Ionworks()
        >>> prior = client.material_property_dataset.to_prior(
        ...     "dataset-uuid",
        ...     column="particle radius",
        ...     parameter_name="Negative particle radius [m]",
        ...     rel_std=0.1,
        ... )
        >>> fit = iws.DataFit(objectives=..., priors={prior.name: prior})

        A diffusivity spans decades, so use a lognormal:

        >>> prior = client.material_property_dataset.to_prior(
        ...     "dataset-uuid",
        ...     column="diffusion coefficient",
        ...     parameter_name="Negative particle diffusivity [m2.s-1]",
        ...     rel_std=0.5,
        ...     distribution="lognormal",
        ... )
        """
        import ionworks_schema as iws  # lazy: keeps `import ionworks` light

        if (std is None) == (rel_std is None):
            raise ValueError(
                "exactly one of `std` or `rel_std` is required: a scalar has no "
                "spread of its own, so the prior's width must be supplied."
            )
        if distribution not in ("normal", "lognormal"):
            raise ValueError(
                f"unknown distribution {distribution!r}: expected 'normal' or "
                f"'lognormal'."
            )

        value = self._read_scalar(dataset_id, column, row)

        if distribution == "lognormal" and value <= 0:
            raise ValueError(
                f"column {column!r} holds {value!r}, which a lognormal prior "
                f"cannot be centred on: lognormal is defined for strictly "
                f"positive values only. Use distribution='normal' instead."
            )

        # rel_std is a fraction of log(X) for a lognormal, of the value itself
        # for a normal.
        if std is not None:
            width = std
        elif distribution == "lognormal":
            width = rel_std
        else:
            width = rel_std * abs(value)

        # Ordered before `mean` uses it, so the message beats pydantic's.
        if not math.isfinite(width) or width <= 0:
            raise ValueError(
                f"prior width must be a positive, finite number; got {width!r}. "
                f"Check `std` / `rel_std`, and note that `rel_std` scales with "
                f"the value, so a value of 0 leaves no width."
            )

        if distribution == "lognormal":
            # No offset on log(value): see this method's Notes for why the
            # regularizer's mode-vs-log-mean gap is deliberately not corrected.
            cls, mean = iws.stats.LogNormal, math.log(value)
        else:
            cls, mean = iws.stats.Normal, value

        return iws.priors.Prior(
            parameter_name,
            cls(mean=mean, std=width),
            regularizer_weight=regularizer_weight,
        )

    def _read_scalar(self, dataset_id: str, column: str, row: int | None) -> float:
        """Read one cell from a property dataset as a float.

        Parameters
        ----------
        dataset_id : str
            The material property dataset UUID.
        column : str
            Name of the column to read.
        row : int | None
            Row index to read. When ``None`` the dataset must hold exactly one
            row, so a multi-row curve cannot be silently reduced to a scalar.

        Returns
        -------
        float
            The stored value.

        Raises
        ------
        ValueError
            If the column is absent, the row is ambiguous or out of range, or
            the value is missing.
        """
        # Straight from the endpoint's column -> values JSON: ``get_data`` would
        # build a DataFrame (pandas under that backend) just to index one cell.
        columns = self.client.get(f"{self._BASE}/{dataset_id}/data")

        if column not in columns:
            raise ValueError(
                f"column {column!r} is not in dataset {dataset_id}. Available "
                f"columns: {', '.join(map(repr, columns))}."
            )
        values = columns[column]

        if row is None:
            if len(values) != 1:
                raise ValueError(
                    f"dataset {dataset_id} has {len(values)} rows, so {column!r} "
                    f"is not a scalar. Pass `row=` to pick one value "
                    f"deliberately."
                )
            row = 0
        elif not -len(values) <= row < len(values):
            raise ValueError(
                f"row {row} is out of range for dataset {dataset_id}, which has "
                f"{len(values)} row(s)."
            )

        value = values[row]
        try:
            value = float(value) if value is not None else None
        except (TypeError, ValueError):
            value = None
        # +inf would otherwise slip past the lognormal's positive-value guard.
        if value is None or not math.isfinite(value):
            raise ValueError(
                f"column {column!r} holds no usable number at row {row} of "
                f"dataset {dataset_id}: found {values[row]!r}. The cell is "
                f"empty, non-numeric, or was not parseable as a finite number "
                f"when the dataset was uploaded."
            )
        return value
