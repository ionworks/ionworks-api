"""Material property client for retrieving tabular property datasets.

This module provides :class:`MaterialPropertyDatasetClient` for reading material property
datasets (e.g. conductivity, diffusivity, and transference number as a function of
electrolyte concentration) stored in the Ionworks backend.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from .models import (
    MaterialPropertyDataset,
    PaginatedList,
    _build_endpoint,
    _parse_list_response,
)
from .validators import DataFrame, get_dataframe_backend


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
