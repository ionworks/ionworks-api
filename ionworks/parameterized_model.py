"""Parameterized model client for managing parameterized battery models.

This module provides the :class:`ParameterizedModelClient` for creating,
reading, and updating parameterized models, which combine a base model with
specific parameter values for a given cell specification.
"""

from __future__ import annotations

from typing import Any

from .models import (
    PaginatedList,
    ParameterizedModel,
    _build_endpoint,
    _parse_list_response,
)


class ParameterizedModelClient:
    """Client for managing parameterized models.

    Parameterized models combine a base model (e.g. SPM, DFN) with specific
    parameter values for a given cell specification.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the ParameterizedModelClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def get(self, parameterized_model_id: str) -> ParameterizedModel:
        """Get a specific parameterized model by ID.

        Uses the non-cell-scoped endpoint so callers don't need the cell spec
        ID just to fetch an existing model.

        Parameters
        ----------
        parameterized_model_id : str
            The ID of the parameterized model to retrieve.

        Returns
        -------
        ParameterizedModel
            The requested parameterized model object.
        """
        endpoint = f"/parameterized_models/{parameterized_model_id}"
        response_data = self.client.get(endpoint)
        return ParameterizedModel(**response_data)

    def list(
        self,
        cell_spec_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> PaginatedList[ParameterizedModel]:
        """List parameterized models for a cell specification.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification to list models for.
        limit : int | None, optional
            Maximum number of models to return per page.
        offset : int | None, optional
            Number of models to skip for pagination.

        Returns
        -------
        PaginatedList[ParameterizedModel]
            A paginated list of parameterized model objects.
        """
        endpoint = _build_endpoint(
            f"/cells/{cell_spec_id}/parameterized_models",
            {"limit": limit, "offset": offset},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, ParameterizedModel)

    def create(self, cell_spec_id: str, data: dict[str, Any]) -> ParameterizedModel:
        """Create a new parameterized model for a cell specification.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification to create the parameterized model
            for.
        data : dict[str, Any]
            Dictionary containing the parameterized model data. Required
            fields: ``name``, ``model_id``. Optional fields: ``description``,
            ``parameters``.

        Returns
        -------
        ParameterizedModel
            The newly created parameterized model object.
        """
        endpoint = f"/cells/{cell_spec_id}/parameterized_models"
        response_data = self.client.post(endpoint, data)
        return ParameterizedModel(**response_data)

    def update(
        self,
        cell_spec_id: str,
        parameterized_model_id: str,
        data: dict[str, Any],
    ) -> ParameterizedModel:
        """Update a parameterized model's name and/or description.

        Parameters
        ----------
        cell_spec_id : str
            The ID of the cell specification the model belongs to.
        parameterized_model_id : str
            The ID of the parameterized model to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports ``name`` and
            ``description``.

        Returns
        -------
        ParameterizedModel
            The updated parameterized model object.
        """
        endpoint = (
            f"/cells/{cell_spec_id}/parameterized_models/{parameterized_model_id}"
        )
        response_data = self.client.patch(endpoint, data)
        return ParameterizedModel(**response_data)

    def get_parameter_values(self, parameterized_model_id: str) -> dict[str, Any]:
        """Get all parameter values from a parameterized model as JSON.

        Returns the complete parameter set, suitable for use as baseline
        parameters in DataFit, parameterization, or optimization workflows.
        Version and citation metadata are excluded.

        Parameters
        ----------
        parameterized_model_id : str
            The ID of the parameterized model to fetch parameters from.

        Returns
        -------
        dict[str, Any]
            All parameterized model parameter values as JSON.
        """
        endpoint = f"/parameterized_models/{parameterized_model_id}/parameter-values"
        return self.client.get(endpoint)

    def get_variable_names(self, parameterized_model_id: str) -> list[str]:
        """Get scalar variable names from a parameterized model.

        Returns the names of all variables where the domain is empty or
        "current collector" (i.e. they are functions of time only).

        Parameters
        ----------
        parameterized_model_id : str
            The ID of the parameterized model to fetch variables from.

        Returns
        -------
        list[str]
            List of variable names with empty or current collector domain.
        """
        endpoint = f"/parameterized_models/{parameterized_model_id}/variable-names"
        return self.client.get(endpoint)
