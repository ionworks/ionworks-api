"""Main client module for the Ionworks API.

This module provides the :class:`Ionworks` client, which is the main entry point
for interacting with the Ionworks API. It handles authentication, request/response
processing, and provides access to all API resources through sub-clients.
"""

from datetime import date, datetime
import gzip
import json as json_mod
import logging
import os
from typing import Any, cast

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry

from ._project_id import resolve_env_project_id
from .analysis import AnalysisClient
from .cell_instance import CellInstanceClient
from .cell_measurement import CellMeasurementClient
from .cell_specification import CellSpecificationClient
from .channel import ChannelClient
from .custom_model import ModelClient
from .cycler import CyclerClient
from .ecm import ECMClient
from .electrolyte import ElectrolyteClient
from .errors import IonworksError
from .job import JobClient
from .lab import LabClient
from .material import MaterialClient
from .material_property_dataset import MaterialPropertyDatasetClient
from .models import CellMeasurement
from .optimization import OptimizationClient
from .organization import OrganizationClient
from .parameterized_model import ParameterizedModelClient
from .pipeline import PipelineClient
from .planned_measurement import PlannedMeasurementClient
from .project import ProjectClient
from .protocol import ProtocolClient
from .raw_data import RawDataClient
from .search import SearchClient
from .simple_pipeline import SimplePipelineClient
from .simulation import SimulationClient
from .site import SiteClient
from .study import StudyClient
from .urls import UrlsClient
from .validators import set_dataframe_backend

logger = logging.getLogger(__name__)

#: Payloads larger than this (bytes) are gzip-compressed before sending.
_GZIP_THRESHOLD = 512 * 1024  # 512 KB


def _json_default(obj: Any) -> Any:
    """Fallback JSON serializer for types not handled by stdlib json.

    Handles ``datetime`` and ``date`` objects (including ``pd.Timestamp``,
    which subclasses ``datetime``) via ``.isoformat()``. Maps ``pd.NaT``
    to ``None`` — ``.replace()`` on a DataFrame does not catch ``pd.NaT``,
    so datetime columns with missing values emit it from ``.to_dict()``.

    Parameters
    ----------
    obj : Any
        Object that failed default serialization.

    Returns
    -------
    Any
        A JSON-serializable representation of ``obj``.

    Raises
    ------
    TypeError
        If ``obj`` cannot be converted to a JSON-serializable type.
    """
    if obj is pd.NaT:
        return None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class _ConnectAbortRetry(Retry):
    """Retry policy that treats a dropped connection as safe to resend.

    urllib3 classifies a server-closed connection (``ProtocolError``, e.g.
    ``RemoteDisconnected`` on a reused keep-alive socket) as a *read* error, so
    it only retries it for idempotent methods. But a dropped connection means
    the request never reached the application, so resending is safe for any
    method. Read timeouts stay idempotent-only: there the server may already
    have processed the request, so resubmitting a POST could duplicate it.
    """

    def _is_connection_error(self, err: Exception) -> bool:
        # Overrides a urllib3 private method (verified against urllib3 2.x).
        # ``test_client_retry`` asserts the method still exists, so a pin-bump
        # that renames or drops it fails CI rather than silently misclassifying.
        return isinstance(err, ProtocolError) or super()._is_connection_error(err)


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
        token: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        """Initialize Ionworks client.

        Authentication uses either a bearer token or an API key. If a token is
        provided (argument or ``IONWORKS_API_TOKEN`` env var) it is sent as an
        ``Authorization: Bearer`` header; otherwise an API key (argument or
        ``IONWORKS_API_KEY`` env var) is sent as an ``X-API-Key`` header. A token
        takes precedence when both are present.

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
        token : str | None
            Bearer token to authenticate with instead of an API key. Sent as an
            ``Authorization: Bearer`` header. Falls back to the
            ``IONWORKS_API_TOKEN`` env var, but only when neither ``token`` nor
            ``api_key`` is passed explicitly: an argument always outranks the
            environment, and a token outranks an API key only within the same
            source. So ``Ionworks(api_key=...)`` authenticates with that key even
            in a shell (or an agent tool subprocess) that exports
            ``IONWORKS_API_TOKEN``.
        organization_id : str | None
            Organization to scope every request to, sent as an
            ``X-Organization-Id`` header. Falls back to the
            ``IONWORKS_ORGANIZATION_ID`` env var. Relevant when the auth
            principal spans multiple organizations (a bearer token usually
            does); with an API key the organization is already fixed by the key
            and this can be left unset. When unset entirely, no header is sent
            and the backend resolves the organization from the credential.
        """
        # Set DataFrame backend (explicit param > env var > default "polars")
        if dataframe_backend is not None:
            set_dataframe_backend(dataframe_backend)

        # Authentication: a bearer token or an API key; the platform accepts
        # either an Authorization: Bearer JWT or an X-API-Key.
        #
        # Precedence is **explicit argument before ambient environment**, and only
        # then token before api_key. An env var must never override a credential
        # the caller passed by hand: environments that export
        # ``IONWORKS_API_TOKEN`` (the agent runtime injects one into every tool
        # subprocess, and developer shells often have one exported) would
        # otherwise silently ignore ``Ionworks(api_key=...)`` and authenticate as
        # whoever the ambient — often short-lived — token belongs to, failing with
        # an opaque 401 once it expires.
        env_token = os.getenv("IONWORKS_API_TOKEN")
        env_api_key = os.getenv("IONWORKS_API_KEY")
        if token or api_key:
            # At least one credential was passed explicitly: use only those, so
            # ambient env vars cannot outrank them.
            self.token = token
            self.api_key = api_key
        else:
            self.token = env_token
            self.api_key = env_api_key
        if not self.token and not self.api_key:
            raise ValueError(
                "Authentication required: provide a token (argument or "
                "IONWORKS_API_TOKEN env var) or an api_key (argument or "
                "IONWORKS_API_KEY env var)"
            )

        # Strip trailing slashes: endpoints are concatenated raw
        # (``f"{api_url}{endpoint}"``) and every endpoint already starts with
        # "/", so a base URL ending in "/" yields a doubled slash. Older
        # ``requests`` (<2.34.0) silently collapsed it; since psf/requests#7315
        # the literal ``//`` goes out on the wire, and FastAPI routes it as a
        # distinct, unmatched path — a 404 with no redirect, indistinguishable
        # from a genuinely missing resource.
        self.api_url = (
            api_url or os.getenv("IONWORKS_API_URL") or "https://api.ionworks.com"
        ).rstrip("/")

        # Resolve default project_id: explicit arg > IONWORKS_PROJECT_ID >
        # deprecated PROJECT_ID (with warning). Unset is allowed.
        self.project_id = project_id or resolve_env_project_id()

        # Optional active organization, sent as an X-Organization-Id header so
        # requests are scoped to that org. Needed when the auth principal (a
        # bearer token) spans multiple orgs; with an API key the org is already
        # fixed by the key. Explicit argument outranks the env var, matching the
        # credential precedence above.
        self.organization_id = organization_id or os.getenv("IONWORKS_ORGANIZATION_ID")

        logger.info(
            "Ionworks client api_url=%s project_id=%s organization_id=%s",
            self.api_url,
            self.project_id,
            self.organization_id,
        )

        # Configure timeout (default: 10 seconds)
        self.request_timeout = timeout if timeout is not None else 10

        # Configure retry strategy (default: maximum 5 retries)
        # Dropped connections are retried for every method (the request never
        # reached the server); read timeouts and 5xx stay idempotent-only via
        # allowed_methods, so a non-idempotent POST/PATCH is never resubmitted.
        max_retries_value = max_retries if max_retries is not None else 5
        retry_strategy = _ConnectAbortRetry(
            total=max_retries_value,  # Maximum number of retries
            backoff_factor=0.3,  # Wait 0.3, 0.6, 1.2, 2.4, 4.8 seconds between retries
            status_forcelist=[500, 502, 503, 504],  # Retry on these HTTP status codes
            allowed_methods=["GET", "DELETE"],  # gates read/status retries only
            raise_on_status=False,  # Don't raise exception, let it be handled below
        )

        # Create HTTP adapter with retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)

        self.session = requests.Session()
        auth_header = (
            {"Authorization": f"Bearer {self.token}"}
            if self.token
            else {"X-API-Key": self.api_key}
        )
        org_header = (
            {"X-Organization-Id": self.organization_id} if self.organization_id else {}
        )
        # When IONWORKS_INTERNAL_CLIENT is set, signal to the backend that
        # storage redirect URLs should not be rewritten for external access —
        # for co-located clients that reach storage directly. No effect against
        # a hosted backend, where signed URLs already use a publicly resolvable
        # host. (Header name must match the backend's INTERNAL_CLIENT_HEADER.)
        internal_header = (
            {"X-Ionworks-Internal-Client": "1"}
            if os.getenv("IONWORKS_INTERNAL_CLIENT")
            else {}
        )
        self.session.headers.update(
            {
                **auth_header,
                **org_header,
                **internal_header,
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
        self.simple_pipeline = SimplePipelineClient(self)
        self.cell_spec = CellSpecificationClient(self)
        self.cell_instance = CellInstanceClient(self)
        self.cell_measurement = CellMeasurementClient(self)
        self.site = SiteClient(self)
        self.cycler = CyclerClient(self)
        self.channel = ChannelClient(self)
        self.lab = LabClient(self)
        self.planned_measurement = PlannedMeasurementClient(self)
        self.simulation = SimulationClient(self)
        self.project = ProjectClient(self)
        self.model = ModelClient(self)
        self.parameterized_model = ParameterizedModelClient(self)
        self.study = StudyClient(self)
        self.optimization = OptimizationClient(self)
        self.organization = OrganizationClient(self)
        self.protocol = ProtocolClient(self)
        self.ecm = ECMClient(self)
        self.urls = UrlsClient(self)
        self.material = MaterialClient(self)
        self.material_property_dataset = MaterialPropertyDatasetClient(self)
        self.search = SearchClient(self)
        self.analysis = AnalysisClient(self)
        self.electrolyte = ElectrolyteClient(self)
        self.raw_data = RawDataClient(self)
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
                raw = json_mod.dumps(json_payload, default=_json_default).encode()
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

    def post_multipart(
        self,
        endpoint: str,
        files: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """POST to ``endpoint`` with optional multipart files and query params.

        When ``files`` is ``None`` or empty, the request is sent as a plain
        POST with only query-string params (no body, no multipart
        ``Content-Type``).

        Parameters
        ----------
        endpoint : str
            API endpoint path.
        files : dict[str, Any] or None, optional
            Files mapping in the form expected by ``requests`` (e.g.
            ``{"file": (filename, fileobj, content_type)}``). Pass ``None``
            for endpoints that take only query-string params.
        params : dict[str, Any] | None, optional
            Query string parameters.

        Returns
        -------
        Any
            Parsed JSON response body.
        """
        url = f"{self.api_url}{endpoint}"
        # ``requests`` adds a multipart Content-Type whenever ``files`` is a
        # truthy dict, so falsy/None reliably degrades to a plain POST.
        # Override the session's JSON Content-Type so the boundary is computed
        # automatically when ``files_arg`` is set.
        files_arg = files or None
        headers = {"Content-Type": None} if files_arg is not None else None
        try:
            response = self.session.post(
                url,
                files=files_arg,
                params=params,
                headers=headers,
                timeout=(10, 300),
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
        method: str = "POST",
    ) -> Any:
        """Send a ``multipart/form-data`` request.

        Goes through the session (not bare ``requests.post``) so the
        upload gets the retry-aware adapter. ``requests`` builds the
        ``Content-Type`` header (with the right boundary) only when
        none is supplied, so the session's JSON ``Content-Type`` is
        dropped for this request via ``headers={"Content-Type": None}``.

        Parameters
        ----------
        endpoint : str
            API endpoint path, e.g. ``"/models/upload-custom"``.
        data : dict[str, Any] | None
            Form fields to send alongside the file(s).
        files : dict[str, Any] | None
            ``requests``-style ``files`` mapping (typically
            ``{"file": (filename, fileobj, content_type)}``).
        method : str, optional
            HTTP method to use. Defaults to ``"POST"`` for create-style
            endpoints; pass ``"PATCH"`` for endpoints that replace an
            existing resource's file.

        Returns
        -------
        Any
            Parsed JSON response, or the raw ``Response`` if the
            response body isn't JSON.
        """
        url = f"{self.api_url}{endpoint}"
        try:
            response = self.session.request(
                method,
                url,
                headers={"Content-Type": None},
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

    def whoami(self) -> dict[str, Any]:
        """Return the user profile the configured API key resolves to.

        Hits ``GET /users/me`` and is the recommended way to debug API-key
        issues: the ``authorized_organization`` field is the organization
        this request is authorized as — for SDK requests, that's the org
        the configured API key was issued for, which is the source of
        truth for permission checks on every call the SDK makes. The
        ``organizations`` field is the user's full membership list and
        is a different question.

        Returns
        -------
        dict[str, Any]
            User profile payload. Notable keys:

            - ``id`` : user id
            - ``email`` : user email
            - ``authorized_organization`` : ``{"id": ..., "name": ...}``
              or ``None`` — the org this request is authorized as. For
              SDK requests, the org the API key is scoped to.
            - ``organizations`` : list of every org the user belongs to.

        Raises
        ------
        IonworksError
            ``status_code=401`` if the configured API key is missing,
            wrong, or expired.

        Examples
        --------
        >>> me = client.whoami()
        >>> me["authorized_organization"]
        {'id': '...', 'name': 'Acme Battery'}
        """
        return cast(dict[str, Any], self.get("/users/me"))

    @staticmethod
    def _resolve_unique_by_name(results, resource_label: str, name: str) -> Any:
        """Return the single result matching ``name``, or raise a clear error.

        Parameters
        ----------
        results : iterable
            The result of a sub-client ``.list(..., name_exact=name)`` call.
        resource_label : str
            Human-readable resource name for error messages (e.g.
            ``"cell instance"``).
        name : str
            The name that was looked up, echoed back in error messages.

        Returns
        -------
        Any
            The single matching entity.

        Raises
        ------
        IonworksError
            ``status_code=404`` if nothing matched, or ``status_code=409`` if
            more than one entity shares the name (resolve by id instead).
        """
        matches = list(results)
        if not matches:
            raise IonworksError(
                f"No {resource_label} named '{name}' found", status_code=404
            )
        if len(matches) > 1:
            raise IonworksError(
                f"Multiple {resource_label}s named '{name}' found; "
                "resolve by id instead",
                status_code=409,
            )
        return matches[0]

    def resolve_measurement(
        self,
        cell_specification: str,
        cell_instance: str,
        measurement: str,
        *,
        project_id: str | None = None,
    ) -> CellMeasurement:
        """Resolve a measurement from human-readable names.

        Walks the spec -> instance -> measurement hierarchy by name, filtering
        each level server-side via ``name_exact``, and returns the resolved
        measurement. Saves callers from hand-walking the three list endpoints.

        Parameters
        ----------
        cell_specification : str
            Exact name of the cell specification.
        cell_instance : str
            Exact name of the cell instance within that specification.
        measurement : str
            Exact name of the measurement within that instance.
        project_id : str | None, optional
            Project to resolve the specification within. Defaults to the
            client's project. Spec names are unique per project, not per
            organization, so resolving without a project would report a name
            shared with a sibling project as ambiguous.

        Returns
        -------
        CellMeasurement
            The resolved measurement (use ``.id`` for the measurement id).

        Raises
        ------
        IonworksError
            ``status_code=404`` if any level has no match, or ``409`` if a name
            is ambiguous within its parent.
        ValueError
            If no ``project_id`` is given and the client has no default.

        Examples
        --------
        >>> m = client.resolve_measurement("Cell A spec", "Cell A #1", "RPT 0")
        >>> m.id
        '...'
        """
        # ``cell_spec.list`` requires a project for an exact-name lookup and
        # falls back to the client default, so passing project_id straight
        # through keeps the resolution rule in one place.
        spec = self._resolve_unique_by_name(
            self.cell_spec.list(name_exact=cell_specification, project_id=project_id),
            "cell specification",
            cell_specification,
        )
        instance = self._resolve_unique_by_name(
            self.cell_instance.list(spec.id, name_exact=cell_instance),
            "cell instance",
            cell_instance,
        )
        return self._resolve_unique_by_name(
            self.cell_measurement.list(instance.id, name_exact=measurement),
            "cell measurement",
            measurement,
        )

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
