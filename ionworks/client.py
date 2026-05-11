"""Main client module for the Ionworks API.

This module provides the :class:`Ionworks` client, which is the main entry point
for interacting with the Ionworks API. It handles authentication, request/response
processing, and provides access to all API resources through sub-clients.
"""

import gzip
import json as json_mod
import os
from typing import Any, cast

from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ._project_id import resolve_env_project_id
from .cell_instance import CellInstanceClient
from .cell_measurement import CellMeasurementClient
from .cell_specification import CellSpecificationClient
from .custom_model import ModelClient
from .errors import IonworksError
from .job import JobClient
from .optimization import OptimizationClient
from .parameterized_model import ParameterizedModelClient
from .pipeline import PipelineClient
from .project import ProjectClient
from .protocol import ProtocolClient
from .simulation import SimulationClient
from .study import StudyClient
from .urls import UrlsClient
from .validators import set_dataframe_backend

#: Payloads larger than this (bytes) are gzip-compressed before sending.
_GZIP_THRESHOLD = 512 * 1024  # 512 KB


class Ionworks:
    """Client for interacting with the Ionworks API.

    Handles authentication, request/response processing, and provides access
    to all API resources through sub-clients.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        project_id: str | None = None,
        dataframe_backend: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        """Initialize Ionworks client.

        Parameters
        ----------
        api_key : str | None
            API key. If not provided, will look for IONWORKS_API_KEY env var.
        api_url : str | None
            API URL. If not provided, will look for IONWORKS_API_URL env var.
        project_id : str | None
            Default project ID to use for sub-client methods that take a
            ``project_id`` argument. If not provided, will look for the
            ``IONWORKS_PROJECT_ID`` env var (falling back to the deprecated
            ``PROJECT_ID`` env var with a ``DeprecationWarning``). May be left
            unset; methods that need a project will raise a clear error if
            none is available.
        dataframe_backend : str | None
            DataFrame backend for returned data: "polars" or "pandas".
            If not provided, uses IONWORKS_DATAFRAME_BACKEND env var
            (defaults to "polars").
        timeout : int | None
            Request timeout in seconds. Defaults to 10 seconds if not provided.
        max_retries : int | None
            Maximum number of retries for failed requests. Defaults to 5 if not
            provided. Retries occur on connection errors, timeouts, and 5xx
            server errors.
        """
        load_dotenv()

        # Set DataFrame backend (explicit param > env var > default "polars")
        if dataframe_backend is not None:
            set_dataframe_backend(dataframe_backend)

        self.api_key = api_key or os.getenv("IONWORKS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key must be provided either as argument or in IONWORKS_API_KEY "
                "environment variable"
            )

        self.api_url = (
            api_url or os.getenv("IONWORKS_API_URL") or "https://api.ionworks.com"
        )

        # Resolve default project_id: explicit arg > IONWORKS_PROJECT_ID >
        # deprecated PROJECT_ID (with warning). Unset is allowed.
        self.project_id = project_id or resolve_env_project_id()

        # Configure timeout (default: 10 seconds)
        self.request_timeout = timeout if timeout is not None else 10

        # Configure retry strategy (default: maximum 5 retries)
        # Retry on connection errors, timeouts, and 5xx server errors
        max_retries_value = max_retries if max_retries is not None else 5
        retry_strategy = Retry(
            total=max_retries_value,  # Maximum number of retries
            backoff_factor=0.3,  # Wait 0.3, 0.6, 1.2, 2.4, 4.8 seconds between retries
            status_forcelist=[500, 502, 503, 504],  # Retry on these HTTP status codes
            allowed_methods=[
                "GET",
                "DELETE",
            ],  # Only retry idempotent methods; don't retry POST/PATCH
            raise_on_status=False,  # Don't raise exception, let it be handled below
        )

        # Create HTTP adapter with retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        # Mount adapter for both HTTP and HTTPS
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Initialize components
        self.job = JobClient(self)
        self.pipeline = PipelineClient(self)
        self.cell_spec = CellSpecificationClient(self)
        self.cell_instance = CellInstanceClient(self)
        self.cell_measurement = CellMeasurementClient(self)
        self.simulation = SimulationClient(self)
        self.project = ProjectClient(self)
        self.model = ModelClient(self)
        self.parameterized_model = ParameterizedModelClient(self)
        self.study = StudyClient(self)
        self.optimization = OptimizationClient(self)
        self.protocol = ProtocolClient(self)
        self.urls = UrlsClient(self)
        # Backwards-compatible alias
        self.measurement = self.cell_measurement

    def _raise_for_http_error(self, e: requests.exceptions.HTTPError, url: str) -> None:
        """Extract error details from an HTTP error response and raise."""
        correlation_id = e.response.headers.get("x-correlation-id")
        if correlation_id:
            print(f"x-correlation-id: {correlation_id}")
        try:
            error_body = e.response.json()
            if "error_code" in error_body:
                error_detail = error_body
            else:
                error_detail = error_body.get("detail", str(e))
        except requests.exceptions.JSONDecodeError:
            error_detail = str(e)
        raise IonworksError(error_detail, status_code=e.response.status_code) from None

    def _raise_for_request_error(
        self, e: requests.exceptions.RequestException, url: str
    ) -> None:
        """Extract error details from a general request exception and raise."""
        error_msg = f"Error during request to {url}"
        if hasattr(e, "response") and e.response is not None:
            error_msg += f" (status code: {e.response.status_code})"
            try:
                error_detail = e.response.json().get("detail", e.response.text)
                error_msg += f": {error_detail}"
            except requests.exceptions.JSONDecodeError:
                error_msg += f": {e.response.text}"
        else:
            error_msg += f": {e!s}"
        raise IonworksError(error_msg) from None

    def request(
        self, method: str, endpoint: str, json_payload: dict[str, Any] | None = None
    ) -> Any:
        """Make a request to the Ionworks API with standardized error handling.

        Requests use the configured timeout and will retry up to the configured
        maximum number of times on connection errors, timeouts, and 5xx server
        errors.
        """
        url = f"{self.api_url}{endpoint}"
        try:
            # Gzip-compress large payloads to reduce upload time.
            extra_kwargs: dict[str, Any] = {}
            if json_payload is not None:
                raw = json_mod.dumps(json_payload).encode()
                if len(raw) >= _GZIP_THRESHOLD:
                    extra_kwargs["data"] = gzip.compress(raw, compresslevel=1)
                    del raw
                    extra_kwargs["headers"] = {
                        "Content-Encoding": "gzip",
                        "Content-Type": "application/json",
                    }
                else:
                    extra_kwargs["data"] = raw
            response = self.session.request(
                method, url, timeout=self.request_timeout, **extra_kwargs
            )
            response.raise_for_status()

            # For DELETE operations, don't try to parse JSON if response is empty
            if method.upper() == "DELETE":
                return None

            # Return JSON response if content type is JSON and response has content
            if (
                response.headers.get("Content-Type") == "application/json"
                and response.text
            ):
                return response.json()
            return response
        except requests.exceptions.HTTPError as e:
            self._raise_for_http_error(e, url)
        except requests.exceptions.RequestException as e:
            self._raise_for_request_error(e, url)

    def request_raw(
        self,
        method: str,
        endpoint: str,
        timeout: tuple[int, int] | int | None = None,
    ) -> bytes:
        """Make a request and return raw response bytes.

        Intended for endpoints that return binary data or redirect to storage.
        Uses the session (which follows redirects automatically), so auth
        headers are sent on the initial request and stripped on cross-origin
        redirects.

        Parameters
        ----------
        method : str
            HTTP method (e.g. "GET").
        endpoint : str
            API endpoint path (e.g. "/cell_measurements/{id}/time_series").
        timeout : tuple[int, int] | int | None, optional
            Request timeout. Defaults to (10, 300) -- 10s connect, 300s read.

        Returns
        -------
        bytes
            Raw response body.
        """
        url = f"{self.api_url}{endpoint}"
        effective_timeout = timeout if timeout is not None else (10, 300)
        try:
            response = self.session.request(method, url, timeout=effective_timeout)
            response.raise_for_status()
            return response.content
        except requests.exceptions.HTTPError as e:
            self._raise_for_http_error(e, url)
        except requests.exceptions.RequestException as e:
            self._raise_for_request_error(e, url)

    def get(self, endpoint: str) -> Any:
        """Make a GET request using the request helper."""
        return self.request("GET", endpoint)

    def post(self, endpoint: str, json_payload: dict[str, Any]) -> Any:
        """Make a POST request using the request helper."""
        return self.request("POST", endpoint, json_payload=json_payload)

    def patch(self, endpoint: str, json_payload: dict[str, Any]) -> Any:
        """Make a PATCH request using the request helper."""
        return self.request("PATCH", endpoint, json_payload=json_payload)

    def delete(self, endpoint: str) -> None:
        """Make a DELETE request using the request helper."""
        self.request("DELETE", endpoint)

    def upload_multipart(
        self,
        endpoint: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        """POST a ``multipart/form-data`` request.

        ``requests`` builds the ``Content-Type`` header (with the
        right boundary) only when none is supplied, so we copy the
        session headers and drop the JSON ``Content-Type`` for this
        request only.

        Parameters
        ----------
        endpoint : str
            API endpoint path, e.g. ``"/models/upload-custom"``.
        data : dict[str, Any] | None
            Form fields to send alongside the file(s).
        files : dict[str, Any] | None
            ``requests``-style ``files`` mapping (typically
            ``{"file": (filename, fileobj, content_type)}``).

        Returns
        -------
        Any
            Parsed JSON response, or the raw ``Response`` if the
            response body isn't JSON.
        """
        url = f"{self.api_url}{endpoint}"
        headers = {k: v for k, v in self.session.headers.items() if k != "Content-Type"}
        try:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                files=files,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            if (
                response.headers.get("Content-Type", "").startswith("application/json")
                and response.text
            ):
                return response.json()
            return response
        except requests.exceptions.HTTPError as e:
            self._raise_for_http_error(e, url)
        except requests.exceptions.RequestException as e:
            self._raise_for_request_error(e, url)

    def health_check(self) -> dict[str, Any]:
        """Check the health of the Ionworks API.

        Returns
        -------
        dict[str, Any]
            Health check response.
        """
        response_data = self.get("/healthz")
        return cast(dict[str, Any], response_data)

    def capabilities(self) -> dict[str, Any]:
        """Fetch platform capabilities and domain context.

        Returns domain knowledge (battery data hierarchy, key
        concepts), authentication info, and pointers to JSON
        Schema endpoints.

        Returns
        -------
        dict[str, Any]
            Capabilities including ``domain_context``,
            ``schemas``, ``openapi_spec``, and
            ``authentication``.
        """
        return cast(dict[str, Any], self.get("/discovery/capabilities"))

    def schema(self, name: str) -> dict[str, Any]:
        """Fetch a discovery schema by name.

        Parameters
        ----------
        name : str
            Schema to fetch. Supported values:

            - ``"data"`` — cell data hierarchy (specifications,
              instances, measurements, steps, time_series).
            - ``"protocol"`` — Universal Cycler Protocol (UCP)
              JSON Schema.

        Returns
        -------
        dict[str, Any]
            The requested schema.

        Raises
        ------
        ValueError
            If *name* is not a recognised schema.
        """
        allowed = {"data", "protocol"}
        if name not in allowed:
            raise ValueError(
                f"Unknown schema {name!r}. Choose from: {', '.join(sorted(allowed))}"
            )
        return cast(dict[str, Any], self.get(f"/discovery/schemas/{name}"))

    def pybamm_models(self) -> dict[str, Any]:
        """List pybamm/ionworks model classes and option values.

        Use this to decide whether you can express your model as a config
        (``client.model.create({"config": {"pybamm_model": ...,
        "options": {...}}})``) or whether you need
        ``client.model.upload_custom`` for a custom
        ``pybamm.BaseModel`` subclass.

        Returns
        -------
        dict[str, Any]
            ``{"pybamm_models": {<module>: [<class names>]},
            "ionworks_models": {...}, "options": {<name>: [<allowed
            values>]}, "pybamm_version": "..."}``.
        """
        return cast(dict[str, Any], self.get("/discovery/pybamm_models"))

    def validate_pybamm_model_config(
        self,
        pybamm_model: str,
        options: dict[str, Any] | None = None,
        module: str = "lithium_ion",
    ) -> dict[str, Any]:
        """Check whether a (model, options) combination is buildable.

        Tries to instantiate the model server-side via the same code path
        the simulation pipeline uses. Returns ``{"valid": True}`` on
        success, or ``{"valid": False, "error": <message>, "error_type":
        <exception class>}`` if pybamm/ionworks rejects the combination.
        Nothing is persisted.

        Parameters
        ----------
        pybamm_model : str
            Class name (e.g. ``"DFN"``, ``"SPM"``, ``"ECM"``). One of the
            names listed by :meth:`pybamm_models`.
        options : dict[str, Any] | None, optional
            Options dict passed to the model constructor.
        module : str, optional
            PyBaMM submodule (``"lithium_ion"`` or ``"lead_acid"``).
            Ignored for ionworks-specific models. Defaults to
            ``"lithium_ion"``.

        Returns
        -------
        dict[str, Any]
            Validation result.
        """
        body: dict[str, Any] = {
            "pybamm_model": pybamm_model,
            "module": module,
            "options": options or {},
        }
        return cast(
            dict[str, Any],
            self.post("/discovery/pybamm_models/validate", body),
        )
