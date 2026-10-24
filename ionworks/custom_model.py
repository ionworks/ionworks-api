"""Custom model client for managing battery models and custom variables.

This module provides the :class:`ModelClient` for creating, reading,
updating, and deleting custom battery models within an organization, as well
as adding custom variables to those models.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from typing import IO, Any, Literal, NamedTuple

from ._model_config import normalize_model_config
from ._parameters import normalize_parameters
from ._project_id import resolve_project_id
from ._simulation_settings import normalize_simulation_settings
from .errors import IonworksError
from .models import (
    Model,
    PaginatedList,
    ParameterizedModel,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)

#: Allowed values for the ``chemistry`` field on uploaded custom models.
#: Mirrors the ``ModelChemistry`` Literal on the backend.
ModelChemistry = Literal["lithium_ion", "lithium_sulfur", "ecm", "generic"]


class ZooImport(NamedTuple):
    """The records :meth:`ModelClient.create_from_zoo` creates."""

    model: Model
    parameterized_model: ParameterizedModel


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

    def get_simulation_settings(self, model_id: str) -> dict[str, Any]:
        """Return a model's persisted simulation settings, ready to fold into a fit.

        Standard datafit / validation objective configs are hand-authored by the
        caller, so a saved model's persisted mesh + solver are not applied
        automatically the way they are for plain simulations and design
        optimization. Use this to fetch them and merge them into an objective's
        ``options["simulation_kwargs"]`` so the fit/validation runs with the same
        discretization the model was configured with::

            sim_kwargs = client.model.get_simulation_settings(model_id)
            objective = iws.objectives.CurrentDriven(
                data_input="…",
                options={"model": {"type": "SPMe"}, "simulation_kwargs": sim_kwargs},
            )

        For a validation against a parameterized model, read
        ``client.parameterized_model.get(pm_id).simulation_settings`` instead
        (its parameter-specific settings take precedence over the base model's).

        Parameters
        ----------
        model_id : str
            The ID of the (base) model.

        Returns
        -------
        dict[str, Any]
            The flat ``simulation_settings`` bag (``var_pts`` / ``submesh_types``
            / ``spatial_methods`` / ``solver``), or an empty dict if the model has
            none persisted.
        """
        return self.get(model_id).simulation_settings or {}

    def list(
        self,
        limit: int | None = None,
        offset: int | None = None,
        *,
        project_id: str | None = None,
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
        """List a project's models with optional filtering.

        Parameters
        ----------
        limit : int | None, optional
            Maximum number of models to return per page.
        offset : int | None, optional
            Number of models to skip for pagination.
        project_id : str | None, optional
            Project whose models to list. Defaults to the client's project
            (``IONWORKS_PROJECT_ID``).
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

        Raises
        ------
        ValueError
            If no ``project_id`` is given and the client has no default.
        """
        project_id = resolve_project_id(self.client, project_id)
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
            {
                "project_id": project_id,
                "limit": limit,
                "offset": offset,
                **filter_params,
            },
        )
        response_data = self.client.get(endpoint)
        return _parse_list_response(response_data, Model)

    def create(self, data: dict[str, Any] | None = None) -> Model:
        """Create a new model in a project.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary containing the model data. Required fields: ``name``,
            ``config``. Optional fields: ``project_id`` (defaults to the client's
            project), ``description``, ``pybamm_version``,
            ``simulation_settings``.

            ``config`` may be a ``{"type": ...}`` dict or a built-in
            ``pybamm.BaseModel`` instance (e.g. ``pybamm.lithium_ion.SPMe()``),
            which is serialized for you the same way ``ionworks_schema`` accepts
            a pybamm model as a fit's model. Custom pybamm models go through
            :meth:`upload_custom` instead.

            ``simulation_settings`` is a persistent bag of pybamm simulation
            kwargs (``var_pts`` / ``submesh_types`` / ``spatial_methods`` /
            ``solver``) re-applied whenever the model is simulated. Build it from
            live pybamm objects with the schema wrapper::

                import ionworks_schema as iws
                import pybamm

                settings = iws.models.SimulationSettings(
                    var_pts={"r_n": 16, "r_p": 16},
                    submesh_types={
                        "negative particle": pybamm.MeshGenerator(
                            pybamm.Exponential1DSubMesh, {"side": "right"}
                        ),
                    },
                )
                client.model.create({"name": "SPMe", "config": {"type": "SPMe"},
                                     "simulation_settings": settings})

            The ``SimulationSettings`` object is serialized for you; an
            already-serialized config ``dict`` (``settings.to_config()``) is
            equally accepted.

        Returns
        -------
        Model
            The newly created model object.

        Raises
        ------
        ValueError
            If ``data`` has no ``project_id`` and the client has no default.
        """
        endpoint = self._BASE
        # Copied so filling in the default project never mutates the caller's dict.
        payload = {**normalize_model_config(normalize_simulation_settings(data))}
        payload["project_id"] = resolve_project_id(
            self.client, payload.get("project_id")
        )
        response_data = self.client.post(endpoint, payload)
        return Model(**response_data)

    def upload_custom(
        self,
        model: Any,
        *,
        name: str,
        chemistry: ModelChemistry = "lithium_ion",
        description: str | None = None,
        project_id: str | None = None,
    ) -> Model:
        """Upload a custom PyBaMM model into a project.

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
        project_id : str | None, optional
            Project to upload into. Defaults to the client's project
            (``IONWORKS_PROJECT_ID``).

        Returns
        -------
        Model
            The created model record (``is_custom_model=True``).

        Raises
        ------
        IonworksError
            On any HTTP error from the upload endpoint.
        ValueError
            If no ``project_id`` is given and the client has no default.

        Notes
        -----
        ``Serialise().serialise_custom_model(model)`` returns a dict that
        contains ``EventType`` enums which aren't JSON-serialisable. When
        a ``pybamm.BaseModel`` is passed in, this method routes through
        ``save_custom_model(filename=...)`` (which handles the enum
        conversion) via a temp file that is unlinked after upload.
        """
        data = {
            "name": name,
            "chemistry": chemistry,
            "project_id": resolve_project_id(self.client, project_id),
        }
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

    def create_from_zoo(
        self,
        name: str,
        *,
        cell_spec_id: str | None = None,
        options: dict[str, Any] | None = None,
        model_kwargs: dict[str, Any] | None = None,
        parameter_values: Any = None,
        model_name: str | None = None,
        parameterized_model_name: str | None = None,
        description: str | None = None,
        chemistry: ModelChemistry = "lithium_ion",
        project_id: str | None = None,
    ) -> ZooImport:
        """Create a model and a parameterized model from the PyBaMM model zoo.

        Builds the zoo model locally, uploads it as a custom model (see
        :meth:`upload_custom`), then creates a parameterized model for it, under
        ``cell_spec_id`` or in the project. The zoo is not a dependency of this package; install it
        with ``pip install pybamm-model-zoo``, plus the model's ``zoo-<slug>``
        extra if it has one. Under a cell specification, the backend overrides the
        parameterized model's nominal capacity, voltage cut-offs, and current
        with the specification's values.

        Parameters
        ----------
        name : str
            Registered zoo model name, one of ``pybamm_model_zoo.list_models()``
            (e.g. ``"LinearisedSPM"``).
        cell_spec_id : str | None, optional
            Cell specification the parameterized model belongs to, in the same
            project as the model. Defaults to None: the parameterized model has no
            cell specification and keeps the parameter set's capacity and cut-offs.
        options : dict[str, Any] | None, optional
            Model options passed to the zoo model's constructor.
        model_kwargs : dict[str, Any] | None, optional
            Other constructor arguments for zoo models that take them, e.g.
            ``{"num_physical_layers": 4}`` for ``MultiLayer3DThermalSPM``. See
            ``help(pybamm_model_zoo.load(name))`` for a model's arguments.
        parameter_values : pybamm.ParameterValues | str | dict | None, optional
            Parameters for the parameterized model: a ``pybamm.ParameterValues``,
            the name of a pybamm parameter set (e.g. ``"Chen2020"``), or an
            already-serialized parameters dict. Defaults to the zoo model's
            ``default_parameter_values``. A named set or ``ParameterValues`` is
            passed through the model's ``apply_stack_scaling``, for zoo models
            that have one, to add the model's own parameters; a dict is sent as
            is.
        model_name : str | None, optional
            Display name for the model. Defaults to the zoo model's name.
        parameterized_model_name : str | None, optional
            Display name for the parameterized model. Defaults to ``model_name``.
        description : str | None, optional
            Description for both records. Defaults to the zoo entry's summary.
        chemistry : ModelChemistry, optional
            Chemistry tag for the uploaded model, as for :meth:`upload_custom`.
            Defaults to ``"lithium_ion"``.
        project_id : str | None, optional
            Project to create the model in. Defaults to the client's project
            (``IONWORKS_PROJECT_ID``).

        Returns
        -------
        ZooImport
            The created ``model`` and ``parameterized_model``.

        Raises
        ------
        ImportError
            If ``pybamm-model-zoo`` is not installed.
        KeyError
            If ``name`` is not a registered zoo model.
        pybamm_model_zoo.ModelUnavailableError
            If the model's ``zoo-<slug>`` extra is not installed.
        pybamm_model_zoo.ManifestError
            If the zoo entry's manifest is malformed.
        ValueError
            If no ``project_id`` is given and the client has no default.
        IonworksError
            If either record cannot be created. When the server rejects the
            parameterized model (a 4xx), the uploaded model is deleted again, so a
            retry does not duplicate it. On any other failure the outcome is
            unknown, so the model is kept and the error notes its id.

        Examples
        --------
        >>> zoo = client.model.create_from_zoo(
        ...     "LinearisedSPM", cell_spec_id=spec.id, parameter_values="Chen2020"
        ... )  # doctest: +SKIP
        """
        try:
            import pybamm_model_zoo
        except ImportError as e:
            raise ImportError(
                "create_from_zoo needs the PyBaMM model zoo: "
                "pip install pybamm-model-zoo"
            ) from e

        # Fail on a missing project before the slow model build, not after it.
        project_id = resolve_project_id(self.client, project_id)
        entry = pybamm_model_zoo.info(name)
        pybamm_model = entry.load()(options=options, **(model_kwargs or {}))

        if parameter_values is None:
            parameter_values = pybamm_model.default_parameter_values
        elif not isinstance(parameter_values, dict):
            import pybamm

            if isinstance(parameter_values, str):
                parameter_values = pybamm.ParameterValues(parameter_values)
            # A stock set lacks the parameters some zoo models add of their own.
            prepare = getattr(pybamm_model, "apply_stack_scaling", None)
            if prepare is not None:
                parameter_values = prepare(parameter_values.copy())
        # Serialized before the upload, so a failure here leaves nothing behind.
        parameter_values = normalize_parameters({"parameters": parameter_values})[
            "parameters"
        ]

        model_name = model_name or entry.name
        description = description if description is not None else entry.summary
        model = self.upload_custom(
            pybamm_model,
            name=model_name,
            chemistry=chemistry,
            description=description,
            project_id=project_id,
        )
        try:
            parameterized_model = self.client.parameterized_model.create(
                cell_spec_id,
                {
                    "name": parameterized_model_name or model_name,
                    "model_id": model.id,
                    "description": description,
                    "parameters": parameter_values,
                    "project_id": project_id,
                },
            )
        except IonworksError as e:
            # After a timeout or 5xx the PM may exist; deleting would cascade to it.
            if e.status_code is not None and 400 <= e.status_code < 500:
                self.delete(model.id)
            else:
                e.add_note(
                    f"Model '{model_name}' was uploaded as {model.id}; the "
                    "parameterized model may or may not have been created."
                )
            raise
        return ZooImport(model=model, parameterized_model=parameterized_model)

    def serialize(
        self,
        name: str,
        *,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build an ionworks model server-side and return its JSON.

        Asks the API to construct an ionworks model (e.g. ``"ECM"``,
        ``"LumpedSPMR"``, the MSMR models, or ``"GITTModel"``) and serialise
        it to a pybamm ``Serialise`` document. This is the raw counterpart
        to :meth:`download`: it returns the serialized dict without loading
        it, which is handy for saving to disk or re-uploading via
        :meth:`upload_custom`.

        The server builds the model — defined in the licensed
        ``ionworkspipeline`` package — so the caller does **not** need an
        ``ionworkspipeline`` license. Only ionworks models are served;
        standard pybamm models (``"SPM"``, ``"DFN"``, ...) are already
        usable directly with ``pybamm``.

        Parameters
        ----------
        name : str
            Ionworks model class name, e.g. ``"ECM"``, ``"LumpedSPMR"``,
            ``"MSMRFullCellModel"``, ``"GITTModel"``. One of the names listed
            under ``"ionworks_models"`` by ``client.pybamm_models()``.
        options : dict[str, Any] | None, optional
            Options dict passed to the model constructor.

        Returns
        -------
        dict[str, Any]
            The serialized pybamm model document.

        Raises
        ------
        IonworksError
            If the model isn't an ionworks model, can't be built (e.g.
            invalid options), or the response isn't JSON.
        """
        body = {
            "name": name,
            "options": options or {},
        }
        response_data = self.client.post("/discovery/ionworks_models/serialize", body)
        if not isinstance(response_data, dict):
            raise IonworksError(
                "Unexpected non-JSON response from /discovery/ionworks_models/serialize"
            )
        return response_data

    def download(
        self,
        name: str,
        *,
        options: dict[str, Any] | None = None,
        path: str | os.PathLike[str] | None = None,
    ) -> Any:
        """Download an ionworks model as a ready-to-use pybamm model.

        Fetches the serialized model from the API and loads it locally with
        ``pybamm`` — so models defined in the licensed ``ionworkspipeline``
        package (``ECM``, ``LumpedSPMR``, the MSMR models, ``GITTModel``,
        ...) can be used with only ``pybamm`` installed, no
        ``ionworkspipeline`` license required.

        Parameters
        ----------
        name : str
            Ionworks model class name, e.g. ``"ECM"``, ``"LumpedSPMR"``,
            ``"MSMRFullCellModel"``, ``"GITTModel"``. One of the names listed
            under ``"ionworks_models"`` by ``client.pybamm_models()``.
        options : dict[str, Any] | None, optional
            Options dict passed to the model constructor.
        path : str | os.PathLike[str] | None, optional
            If given, also write the serialized model JSON to this path. The
            file can later be re-uploaded with :meth:`upload_custom`.

        Returns
        -------
        pybamm.BaseModel
            The deserialized model, ready to pass to ``pybamm.Simulation``.

        Notes
        -----
        Serialization captures the model's mathematical structure (rhs,
        algebraic, variables, events, initial conditions) but not Python
        helper methods such as ``set_initial_state`` or classmethods.

        When ``path`` is given, the written file may contain bare
        ``Infinity``/``NaN`` tokens (pybamm uses infinite bounds and event
        thresholds). Python's ``json`` and ``Serialise.load_custom_model``
        read these fine, but they are not strictly valid JSON — strict
        parsers (JS ``JSON.parse``, ``jq``, ...) will reject the file.
        """
        model_dict = self.serialize(name, options=options)

        from pybamm.expression_tree.operations.serialise import Serialise

        # Load first so a failed round-trip (e.g. a server/client pybamm
        # version mismatch) doesn't leave an unloadable file at ``path``.
        model = Serialise.load_custom_model(model_dict)
        if path is not None:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(model_dict, fh)
        return model

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
            ``description``, ``pybamm_version``, and ``simulation_settings``
            (a ``SimulationSettings`` object or its config dict; send ``null``
            to clear the persisted settings).

        Returns
        -------
        Model
            The updated model object.
        """
        endpoint = f"{self._BASE}/{model_id}"
        payload = normalize_model_config(normalize_simulation_settings(data))
        response_data = self.client.patch(endpoint, payload)
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
