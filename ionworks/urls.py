"""Frontend URL helpers for the Ionworks web app.

This module provides the :class:`UrlsClient`, which builds links to pages in
the Ionworks web app without requiring callers to hand-construct URLs from
entity IDs.

Every method takes the resource's own ID plus whatever parent IDs the route
requires, with ``project_id`` resolved from the explicit argument, else the
project_id configured on the :class:`~ionworks.Ionworks` client (which itself
comes from the ``IONWORKS_PROJECT_ID`` env var).

The web-app host is derived from the client's ``api_url`` so links point at the
same environment the client talks to (``api.ionworks.com`` →
``app.ionworks.com``, ``stage-api.ionworks.com`` → ``stage.ionworks.com``,
etc.). Set ``IONWORKS_APP_URL`` to override — needed for local development,
where the frontend runs on a different port than the API and so cannot be
derived.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ._project_id import resolve_project_id

if TYPE_CHECKING:
    from .client import Ionworks


_DEFAULT_APP_BASE_URL = "https://app.ionworks.com"


def _derive_app_base_url(api_url: str | None) -> str:
    """Map an API base URL to its corresponding web-app base URL.

    Parameters
    ----------
    api_url : str | None
        The API base URL the client is configured with (e.g.
        ``https://api.ionworks.com``).

    Returns
    -------
    str
        The web-app base URL for the same environment. Production
        (``api.ionworks.com``) maps to ``https://app.ionworks.com``;
        environment-prefixed API hosts (``{env}-api.ionworks.com``, e.g.
        internal and stage) map to ``https://{env}.ionworks.com``. Any other
        host — localhost, opaque Porter hosts, custom domains — falls back to
        the production app URL, since the frontend host is not derivable from
        it (set ``IONWORKS_APP_URL`` to override).
    """
    if not api_url:
        return _DEFAULT_APP_BASE_URL
    host = urlparse(api_url).hostname or ""
    if host == "api.ionworks.com":
        return _DEFAULT_APP_BASE_URL
    match = re.fullmatch(r"([a-z0-9-]+)-api\.ionworks\.com", host)
    if match:
        return f"https://{match.group(1)}.ionworks.com"
    return _DEFAULT_APP_BASE_URL


class UrlsClient:
    """Build links to pages in the Ionworks web app.

    Returned URLs point at the environment the client is configured for,
    derived from its ``api_url`` (override with ``IONWORKS_APP_URL``).
    """

    def __init__(self, client: Ionworks) -> None:
        self._client = client

    @property
    def _app_base_url(self) -> str:
        """The web-app base URL for the client's environment.

        Uses the ``IONWORKS_APP_URL`` env var when set, otherwise derives the
        host from the client's ``api_url``.

        Returns
        -------
        str
            The web-app base URL without a trailing slash.
        """
        override = os.getenv("IONWORKS_APP_URL")
        if override:
            return override.rstrip("/")
        return _derive_app_base_url(getattr(self._client, "api_url", None))

    def _project_path(self, suffix: str, project_id: str | None) -> str:
        """Build an absolute app URL nested under a project.

        Resolves ``project_id`` (explicit argument, else client default) and
        prepends the shared ``/dashboard/projects/{project_id}`` root that
        every resource route lives under.

        Parameters
        ----------
        suffix : str
            Path under the project root, beginning with ``/`` (e.g.
            ``/models/abc``).
        project_id : str | None
            Explicit project ID, or None to use the client default.

        Returns
        -------
        str
            The absolute URL on the client's web-app host.
        """
        project_id = resolve_project_id(self._client, project_id)
        return f"{self._app_base_url}/dashboard/projects/{project_id}{suffix}"

    def project(self, project_id: str | None = None) -> str:
        """Build a link to a project's landing page.

        The web app has no bare project route; opening a project lands on its
        studies page (``/dashboard/projects/{p}/studies``), so this returns
        that URL.

        Parameters
        ----------
        project_id : str | None, optional
            The project ID. Defaults to the project_id set on the Ionworks
            client (resolved from the ``IONWORKS_PROJECT_ID`` env var if not
            passed to the client).

        Returns
        -------
        str
            URL of the project's studies landing page.
        """
        return self._project_path("/studies", project_id)

    def model(self, model_id: str, project_id: str | None = None) -> str:
        """Build a link to a model detail page.

        Parameters
        ----------
        model_id : str
            The model ID.
        project_id : str | None, optional
            The project ID the model belongs to. Defaults to the client's
            project_id.

        Returns
        -------
        str
            URL of the model detail page.
        """
        return self._project_path(f"/models/{model_id}", project_id)

    def parameterized_model(
        self,
        parameterized_model_id: str,
        project_id: str | None = None,
    ) -> str:
        """Build a link to a parameterized model detail page.

        Parameters
        ----------
        parameterized_model_id : str
            The parameterized model ID.
        project_id : str | None, optional
            The project ID the parameterized model belongs to. Defaults to the
            client's project_id.

        Returns
        -------
        str
            URL of the parameterized model detail page.
        """
        return self._project_path(
            f"/parameterized-models/{parameterized_model_id}", project_id
        )

    def optimization(
        self,
        optimization_id: str,
        project_id: str | None = None,
    ) -> str:
        """Build a link to an optimization detail page.

        Parameters
        ----------
        optimization_id : str
            The optimization ID.
        project_id : str | None, optional
            The project ID the optimization belongs to. Defaults to the
            client's project_id.

        Returns
        -------
        str
            URL of the optimization detail page.
        """
        return self._project_path(f"/optimizations/{optimization_id}", project_id)

    def pipeline(self, pipeline_id: str, project_id: str | None = None) -> str:
        """Build a link to a pipeline detail page.

        Parameters
        ----------
        pipeline_id : str
            The pipeline ID.
        project_id : str | None, optional
            The project ID the pipeline belongs to. Defaults to the client's
            project_id.

        Returns
        -------
        str
            URL of the pipeline detail page.
        """
        return self._project_path(f"/pipelines/{pipeline_id}", project_id)

    def study(self, study_id: str, project_id: str | None = None) -> str:
        """Build a link to a study detail page.

        Parameters
        ----------
        study_id : str
            The study ID.
        project_id : str | None, optional
            The project ID the study belongs to. Defaults to the client's
            project_id.

        Returns
        -------
        str
            URL of the study detail page.
        """
        return self._project_path(f"/studies/{study_id}", project_id)

    def simulation(
        self,
        simulation_id: str,
        parameterized_model_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Build a link to a simulation detail page (parameterized-model view).

        A simulation's natural owner is its parameterized model — the
        simulation row carries a ``parameterized_model_id`` (one per
        simulation), so this method builds the parameterized-model-nested
        route. When ``parameterized_model_id`` is not supplied, it is fetched
        from the simulation via ``client.simulation.get(simulation_id)``.

        Parameters
        ----------
        simulation_id : str
            The simulation ID.
        parameterized_model_id : str | None, optional
            The parameterized model the simulation belongs to. When omitted,
            it is derived by fetching the simulation (one network call).
        project_id : str | None, optional
            The project ID the simulation belongs to. Defaults to the client's
            project_id.

        Returns
        -------
        str
            URL of the simulation detail page nested under its parameterized
            model.

        Raises
        ------
        ValueError
            If ``parameterized_model_id`` is not supplied and the fetched
            simulation has no ``parameterized_model_id`` (e.g. a study-only
            simulation). Pass ``parameterized_model_id`` explicitly in that
            case.
        """
        if parameterized_model_id is None:
            simulation = self._client.simulation.get(simulation_id)
            parameterized_model_id = simulation.get("parameterized_model_id")
            if not parameterized_model_id:
                raise ValueError(
                    f"Simulation {simulation_id} has no parameterized_model_id; "
                    "pass parameterized_model_id explicitly."
                )
        return self._project_path(
            f"/parameterized-models/{parameterized_model_id}"
            f"/simulations/{simulation_id}",
            project_id,
        )

    def protocol(self, protocol_id: str, project_id: str | None = None) -> str:
        """Build a link to a protocol detail page.

        Parameters
        ----------
        protocol_id : str
            The protocol (template) ID.
        project_id : str | None, optional
            The project ID the protocol belongs to. Defaults to the client's
            project_id.

        Returns
        -------
        str
            URL of the protocol detail page.
        """
        return self._project_path(f"/protocols/{protocol_id}", project_id)

    def material(self, material_id: str, project_id: str | None = None) -> str:
        """Build a link to a material detail page.

        Parameters
        ----------
        material_id : str
            The material ID.
        project_id : str | None, optional
            The project ID the material belongs to. Defaults to the client's
            project_id.

        Returns
        -------
        str
            URL of the material detail page.
        """
        return self._project_path(f"/materials/{material_id}", project_id)

    def cell_specs(self, project_id: str | None = None) -> str:
        """Build a link to the cell specifications list page.

        Cell specifications have no standalone detail route in the web app;
        this links to their list page within the project's data section.

        Parameters
        ----------
        project_id : str | None, optional
            The project ID. Defaults to the client's project_id.

        Returns
        -------
        str
            URL of the cell specifications list page.
        """
        return self._project_path("/data/specs", project_id)

    def cell_instances(
        self,
        spec_id: str,
        project_id: str | None = None,
    ) -> str:
        """Build a link to the cell instances list page for a specification.

        Cell instances have no standalone detail route in the web app; this
        links to the instances list nested under their cell specification.

        Parameters
        ----------
        spec_id : str
            The cell specification ID the instances belong to.
        project_id : str | None, optional
            The project ID. Defaults to the client's project_id.

        Returns
        -------
        str
            URL of the cell instances list page.
        """
        return self._project_path(f"/data/specs/{spec_id}/instances", project_id)

    def measurement(
        self,
        measurement_id: str,
        project_id: str | None = None,
    ) -> str:
        """Build a link to a cell measurement detail page.

        Parameters
        ----------
        measurement_id : str
            The cell measurement ID.
        project_id : str | None, optional
            The project ID the measurement belongs to. Defaults to the
            project_id set on the Ionworks client (resolved from the
            ``IONWORKS_PROJECT_ID`` env var if not passed to the client).

        Returns
        -------
        str
            URL of the measurement detail page on
            ``https://app.ionworks.com``.
        """
        return self._project_path(f"/data/measurements/{measurement_id}", project_id)
