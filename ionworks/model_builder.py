"""Model builders: recipes that turn a cell specification into a model's parameters.

A recipe reads the cell spec the way a ``DirectEntry`` cell spec entry does: a
``mapping`` of parameter names to dotted paths, such as
``cathode.properties.thickness``. It leaves out ``cell_spec_id``, since a builder
reads whichever cell spec it builds from. Each value is converted to the unit in
its parameter's name, so ``75 um`` becomes ``7.5e-05`` for
``"Positive electrode thickness [m]"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from .models import PaginatedList, _build_endpoint, _parse_list_response

if TYPE_CHECKING:
    import ionworks_schema as iws


class ModelBuilder(BaseModel):
    """A model builder."""

    model_config = ConfigDict(extra="allow")

    id: str
    organization_id: str
    project_id: str
    model_id: str
    name: str
    description: str | None = None
    config: dict[str, Any]


class ReadinessIssue(BaseModel):
    """One reason a cell specification cannot (or only with assumptions) be built."""

    model_config = ConfigDict(extra="allow")

    severity: Literal["error", "warning"]
    code: str
    message: str
    parameter: str | None = None
    path: str | None = None
    element: str | None = None


class ReadinessReport(BaseModel):
    """Result of :meth:`ModelBuilderClient.check`."""

    model_config = ConfigDict(extra="allow")

    ready: bool
    issues: list[ReadinessIssue]
    resolved: int
    total: int


class BuilderReadiness(BaseModel):
    """One builder's readiness for a cell specification."""

    model_config = ConfigDict(extra="allow")

    model_builder_id: str
    model_id: str
    name: str
    ready: bool
    error_count: int
    warning_count: int


class BuildResult(BaseModel):
    """Result of :meth:`ModelBuilderClient.build`."""

    model_config = ConfigDict(extra="allow")

    parameters: dict[str, Any]
    missing: list[str] = []
    issues: list[ReadinessIssue] = []
    parameterized_model_id: str | None = None


def _config_dict(config: iws.Pipeline | dict[str, Any]) -> dict[str, Any]:
    if isinstance(config, dict):
        return config
    import ionworks_schema as iws

    if isinstance(config, iws.Pipeline):
        return config.to_config()
    raise TypeError(
        "A model builder config must be an ionworks_schema.Pipeline or a dict, "
        f"got {type(config).__name__}"
    )


class ModelBuilderClient:
    """Client for model builders."""

    def __init__(self, client: Any) -> None:
        """Initialize the ModelBuilderClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance used for API calls.
        """
        self.client = client

    def list(
        self,
        model_id: str | None = None,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> PaginatedList[ModelBuilder]:
        """List a project's model builders.

        Parameters
        ----------
        model_id : str | None, optional
            Only builders for this model.
        project_id : str | None, optional
            Project to list; defaults to the client's project. Without either,
            every builder readable in the organization is listed.
        limit : int | None, optional
            Page size (at most 100).
        offset : int | None, optional
            Number of builders to skip.

        Returns
        -------
        PaginatedList[ModelBuilder]
            The page of builders, newest first.
        """
        endpoint = _build_endpoint(
            "/model_builders",
            {
                "project_id": project_id or self.client.project_id,
                "model_id": model_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return _parse_list_response(self.client.get(endpoint), ModelBuilder)

    def get(self, model_builder_id: str) -> ModelBuilder:
        """Get a model builder by ID."""
        return ModelBuilder(**self.client.get(f"/model_builders/{model_builder_id}"))

    def create(
        self,
        model_id: str,
        name: str,
        config: iws.Pipeline | dict[str, Any],
        description: str | None = None,
        project_id: str | None = None,
    ) -> ModelBuilder:
        """Create a model builder in a project.

        Parameters
        ----------
        model_id : str
            Model the builder produces parameters for.
        name : str
            Name, unique per model within the project.
        config : iws.Pipeline | dict
            The recipe: a pipeline of entry and calculation elements, as a schema
            object or its ``to_config()`` dict. An entry that reads the cell spec is
            ``{"element_type": "entry", "mapping": {...}}``, without a
            ``cell_spec_id``. Fits and validations are rejected.
        description : str | None, optional
            Free-text description.
        project_id : str | None, optional
            Project the builder belongs to; defaults to the client's project.

        Returns
        -------
        ModelBuilder
            The created builder.

        Raises
        ------
        ValueError
            If no ``project_id`` is given and the client has no default.

        Examples
        --------
        >>> recipe = {"elements": {
        ...     "from cell spec": {
        ...         "element_type": "entry",
        ...         "mapping": {
        ...             "Positive electrode thickness [m]": "cathode.properties.thickness",
        ...         },
        ...     },
        ... }}
        >>> builder = client.model_builder.create(model_id, "Default", recipe)  # doctest: +SKIP
        """
        project_id = project_id or self.client.project_id
        if not project_id:
            raise ValueError(
                "project_id is required or IONWORKS_PROJECT_ID must be set"
            )
        payload: dict[str, Any] = {
            "model_id": model_id,
            "project_id": project_id,
            "name": name,
            "config": _config_dict(config),
        }
        if description is not None:
            payload["description"] = description
        return ModelBuilder(**self.client.post("/model_builders", payload))

    def update(
        self,
        model_builder_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        config: iws.Pipeline | dict[str, Any] | None = None,
    ) -> ModelBuilder:
        """Update one of the organization's own builders; unset fields are kept."""
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if config is not None:
            payload["config"] = _config_dict(config)
        return ModelBuilder(
            **self.client.patch(f"/model_builders/{model_builder_id}", payload)
        )

    def delete(self, model_builder_id: str) -> None:
        """Delete a builder. Parameterized models it produced are kept."""
        self.client.delete(f"/model_builders/{model_builder_id}")

    def check(self, model_builder_id: str, cell_spec_id: str) -> ReadinessReport:
        """Report everything a cell specification lacks for this builder.

        Parameters
        ----------
        model_builder_id : str
            The builder.
        cell_spec_id : str
            The cell specification to check.

        Returns
        -------
        ReadinessReport
            ``ready`` plus every issue, each naming the path in the cell
            specification and the model parameter it feeds.
        """
        response = self.client.post(
            f"/model_builders/{model_builder_id}/check",
            {"cell_spec_id": cell_spec_id},
        )
        return ReadinessReport(**response)

    def build(
        self,
        model_builder_id: str,
        cell_spec_id: str,
        *,
        save: bool = False,
        name: str | None = None,
        description: str | None = None,
        set_as_default: bool = False,
    ) -> BuildResult:
        """Build the model's parameters for a cell specification.

        Parameters
        ----------
        model_builder_id : str
            The builder.
        cell_spec_id : str
            The cell specification to build from.
        save : bool, optional
            Save the result as a parameterized model on the cell specification.
            Refused when any parameter is still missing. Defaults to False.
        name : str | None, optional
            Name for the saved parameterized model; required with ``save``.
        description : str | None, optional
            Description for the saved parameterized model.
        set_as_default : bool, optional
            Make the saved model the cell specification's default.

        Returns
        -------
        BuildResult
            The parameters, any the model still needs (``missing``), the
            readiness warnings, and the saved model's ID when ``save`` is set.
        """
        payload: dict[str, Any] = {
            "cell_spec_id": cell_spec_id,
            "save": save,
            "set_as_default": set_as_default,
        }
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        response = self.client.post(
            f"/model_builders/{model_builder_id}/build", payload
        )
        return BuildResult(**response)

    def dry_run(
        self,
        model_id: str,
        config: iws.Pipeline | dict[str, Any],
        cell_spec_id: str,
    ) -> dict[str, Any]:
        """Run a builder config against a cell spec without saving anything.

        Try a recipe before creating the builder. A spec that is not ready, or a
        calculation that fails, comes back in the result instead of raising.

        Parameters
        ----------
        model_id : str
            The model the config produces parameters for.
        config : ionworks_schema.Pipeline or dict
            The builder config, as it would be passed to :meth:`create`.
        cell_spec_id : str
            The cell specification to run it on.

        Returns
        -------
        dict
            ``{"report", "parameters", "missing", "error"}``: the readiness report,
            the parameters produced (empty when not ready), the parameters the model
            still lacks, and why the calculations failed when they did.

        Examples
        --------
        >>> result = client.model_builder.dry_run(  # doctest: +SKIP
        ...     model_id, pipeline, spec_id
        ... )
        >>> result["report"]["ready"], result["missing"]  # doctest: +SKIP
        (True, [])
        """
        return self.client.post(
            "/model_builders/dry_run",
            {
                "model_id": model_id,
                "config": _config_dict(config),
                "cell_spec_id": cell_spec_id,
            },
        )

    def preview_mapping(
        self, cell_spec_id: str, mapping: dict[str, str]
    ) -> dict[str, dict[str, Any]]:
        """Show what a mapping reads from a cell spec, before and after unit conversion.

        Parameters
        ----------
        cell_spec_id : str
            The cell specification to read from.
        mapping : dict
            Parameter name -> dotted path in the cell spec, as in a recipe entry,
            e.g. ``{"Positive electrode thickness [m]": "cathode.properties.thickness"}``.

        Returns
        -------
        dict
            Parameter name -> ``{"found", "converted", "curve", "issue"}``: the
            spec's value with its own unit (``"75.6 um"``), the value in the
            parameter's unit (``"7.56e-05 m"``), a sketch of a table, and why it
            failed when it did.

        Examples
        --------
        >>> client.model_builder.preview_mapping(spec_id, {  # doctest: +SKIP
        ...     "Positive electrode thickness [m]": "cathode.properties.thickness",
        ... })
        {'Positive electrode thickness [m]': {'found': '75.6 um', 'converted': '7.56e-05 m', 'curve': None, 'issue': None}}
        """
        return self.client.post(
            "/model_builders/preview_mapping",
            {"cell_spec_id": cell_spec_id, "mapping": mapping},
        )

    def calculations_producing(self, parameter: str) -> list[dict[str, Any]]:
        """Calculations, with options filled in, that produce ``parameter``."""
        return self.client.get(
            _build_endpoint(
                "/model_builders/calculations/producing", {"parameter": parameter}
            )
        )

    def resolved_cell_spec(self, cell_spec_id: str) -> dict[str, Any]:
        """The cell spec as a recipe's ``mapping`` paths read it.

        Useful for writing a builder: every leaf here is addressable by its dotted
        path, e.g. ``cathode.properties.thickness`` or, for a material property
        dataset, ``cathode.material.datasets.scalar_parameters.maximum_concentration``.
        """
        return self.client.get(f"/cell_specifications/{cell_spec_id}/resolved")

    def readiness(
        self, cell_spec_id: str, model_id: str | None = None
    ) -> list[BuilderReadiness]:
        """Which builders (optionally for one model) a cell specification is ready for."""
        endpoint = _build_endpoint(
            f"/cell_specifications/{cell_spec_id}/builder_readiness",
            {"model_id": model_id},
        )
        return [BuilderReadiness(**item) for item in self.client.get(endpoint)]
