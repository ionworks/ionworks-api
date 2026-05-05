"""Custom model client for managing battery models and custom variables.

This module provides the :class:`ModelClient` for creating, reading,
updating, and deleting custom battery models within an organization, as well
as adding custom variables to those models.
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import IO, Any, Literal

from .errors import IonworksError
from .models import (
    Model,
    PaginatedList,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)

#: Allowed values for the ``chemistry`` field on uploaded custom models.
#: Mirrors the ``ModelChemistry`` Literal on the backend.
ModelChemistry = Literal["lithium_ion", "lithium_sulfur", "ecm", "generic"]


class ModelClient:
    """Client for managing custom battery models.

    Provides methods to create, read, update, and delete custom models within
    an organization. Also supports adding custom variables to models.
    """

    _BASE = "/models"

    def __init__(self, client: Any) -> None:
        """Initialize the ModelClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    def get(self, model_id: str) -> Model:
        """Get a specific model by ID, including its config.

        Parameters
        ----------
        model_id : str
            The ID of the model to retrieve.

        Returns
        -------
        Model
            The requested model object (includes ``config`` field).
        """
        endpoint = f"{self._BASE}/{model_id}"
        response_data = self.client.get(endpoint)
        return Model(**response_data)

    def list(
        self,
        limit: int | None = None,
        offset: int | None = None,
        *,
        name: str | None = None,
        name_exact: str | None = None,
        created_by_email: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Model]:
        """List models with optional filtering.

        Parameters
        ----------
        limit : int | None, optional
            Maximum number of models to return per page.
        offset : int | None, optional
            Number of models to skip for pagination.
        name : str | None, optional
            Case-insensitive substring match on model name.
        name_exact : str | None, optional
            Exact match on model name.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after : str | None, optional
            ISO datetime; return models created after this time.
        created_before : str | None, optional
            ISO datetime; return models created before this time.
        updated_after : str | None, optional
            ISO datetime; return models updated after this time.
        updated_before : str | None, optional
            ISO datetime; return models updated before this time.
        order_by : str | None, optional
            Column to sort by.
        order : str | None, optional
            Sort direction: ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Model]
            A list of model objects.
        """
        filter_params = _build_filter_params(
            name=name,
            name_exact=name_exact,
            created_by_email=created_by_email,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            order_by=order_by,
            order=order,
        )
        endpoint = _build_endpoint(
            self._BASE,
            {"limit": limit, "offset": offset, **filter_params},
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Model)

    def create(self, data: dict[str, Any] | None = None) -> Model:
        """Create a new model.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the model data. Required fields: ``name``,
            ``config``. Optional fields: ``description``, ``pybamm_version``.

        Returns
        -------
        Model
            The newly created model object.
        """
        endpoint = self._BASE
        response_data = self.client.post(endpoint, data)
        return Model(**response_data)

    def upload_custom(
        self,
        model: Any,
        *,
        name: str,
        chemistry: ModelChemistry = "lithium_ion",
        description: str | None = None,
    ) -> Model:
        """Upload a custom PyBaMM model.

        Serialises a ``pybamm.BaseModel`` subclass instance (or accepts an
        already-serialised JSON file) and POSTs it to
        ``/models/upload-custom`` as multipart form data.

        Parameters
        ----------
        model : pybamm.BaseModel | str | os.PathLike | IO[bytes]
            The model to upload. Either a ``pybamm.BaseModel`` instance
            (will be serialised via ``Serialise().save_custom_model``), a
            path to an existing serialised JSON file, or an open binary
            file object positioned at the start of the JSON content.
        name : str
            Display name for the uploaded model.
        chemistry : ModelChemistry, optional
            Chemistry tag controlling which simulation-pipeline path the
            model goes through. Defaults to ``"lithium_ion"``. Use
            ``"lithium_sulfur"`` for Li-S models, ``"ecm"`` for custom
            ECM models, or ``"generic"`` to opt out of all
            chemistry-specific enrichment.
        description : str | None, optional
            Optional human-readable description.

        Returns
        -------
        Model
            The created model record (``is_custom_model=True``).

        Raises
        ------
        IonworksError
            On any HTTP error from the upload endpoint.

        Notes
        -----
        ``Serialise().serialise_custom_model(model)`` returns a dict that
        contains ``EventType`` enums which aren't JSON-serialisable. When
        a ``pybamm.BaseModel`` is passed in, this method routes through
        ``save_custom_model(filename=...)`` (which handles the enum
        conversion) via a temp file that is unlinked after upload.
        """
        data = {"name": name, "chemistry": chemistry}
        if description is not None:
            data["description"] = description

        cleanup_path: str | None = None
        file_handle: IO[bytes]
        upload_filename = "model.json"

        if hasattr(model, "rhs") and hasattr(model, "variables"):
            # Looks like a pybamm.BaseModel — serialise via temp file.
            from pybamm.expression_tree.operations.serialise import Serialise

            tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
            tmp.close()
            cleanup_path = tmp.name
            Serialise().save_custom_model(model, filename=cleanup_path)
            file_handle = open(cleanup_path, "rb")
            upload_filename = f"{getattr(model, 'name', 'model') or 'model'}.json"
        elif isinstance(model, str | os.PathLike):
            file_handle = open(model, "rb")
            upload_filename = os.path.basename(os.fspath(model))
        elif isinstance(model, io.IOBase) or hasattr(model, "read"):
            file_handle = model  # already-open file-like
        else:
            raise TypeError(
                f"Unsupported model type {type(model).__name__!r}: expected a "
                "pybamm.BaseModel, a filesystem path, or an open binary file."
            )

        try:
            response = self.client.upload_multipart(
                f"{self._BASE}/upload-custom",
                data=data,
                files={"file": (upload_filename, file_handle, "application/json")},
            )
        finally:
            if cleanup_path is not None:
                try:
                    file_handle.close()
                except Exception:
                    pass
                try:
                    os.unlink(cleanup_path)
                except OSError:
                    pass

        if not isinstance(response, dict):
            raise IonworksError(
                "Unexpected non-JSON response from /models/upload-custom"
            )
        return Model(**response)

    def update(
        self,
        model_id: str,
        data: dict[str, Any] | None = None,
    ) -> Model:
        """Update an existing model.

        Parameters
        ----------
        model_id : str
            The ID of the model to update.
        data : dict[str, Any]
            Dictionary containing the fields to update. Supports ``name``,
            ``description``, ``pybamm_version``.

        Returns
        -------
        Model
            The updated model object.
        """
        endpoint = f"{self._BASE}/{model_id}"
        response_data = self.client.patch(endpoint, data)
        return Model(**response_data)

    def delete(self, model_id: str) -> None:
        """Delete a model by ID.

        Parameters
        ----------
        model_id : str
            The ID of the model to delete.
        """
        endpoint = f"{self._BASE}/{model_id}"
        self.client.delete(endpoint)

    def add_custom_variable(
        self,
        model_id: str,
        data: dict[str, Any] | None = None,
    ) -> Model:
        """Add a custom variable to a model.

        Parameters
        ----------
        model_id : str
            The ID of the model to add the custom variable to.
        data : dict[str, Any]
            Dictionary containing the custom variable data. Required fields:
            ``name``, ``expression``.

        Returns
        -------
        Model
            The updated model object (includes config with the new variable).
        """
        endpoint = f"{self._BASE}/{model_id}/custom-variables"
        response_data = self.client.post(endpoint, data)
        return Model(**response_data)
