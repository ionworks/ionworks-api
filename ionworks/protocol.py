"""Protocol client for authoring, saving, and converting UCP protocols.

Provides :class:`ProtocolClient` for the whole protocol lifecycle: writing a
protocol (by hand or by parsing a vendor file), validating it, saving it to a
project so simulations and planned measurements can reference it by id, and
converting it back out to a vendor-native protocol file (Maccor, Arbin,
Neware, BioLogic BT-Test, Novonix).

Saved protocols are stored as ``experiment_template`` rows server-side; the
SDK calls them protocols throughout.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

from ._project_id import resolve_project_id
from .models import (
    PaginatedList,
    ParsedProtocol,
    Protocol,
    _build_endpoint,
    _build_filter_params,
    _parse_list_response,
)

ConversionTarget = Literal["maccor", "arbin", "neware", "biologic_bttest", "novonix"]

#: Heavy columns omitted from list responses unless named in ``include``.
INCLUDABLE_FIELDS = (
    "protocol_config",
    "parameters_schema",
    "time_series_spec",
    "metrics_spec",
    "plot_options",
    "source_protocol",
)


@dataclass
class ConvertResult:
    """Result of converting a UCP to a vendor-native protocol file."""

    #: The vendor target the protocol was converted for.
    target: str
    #: Suggested filename for the primary artifact (with extension).
    primary_filename: str
    #: Raw bytes of the primary artifact.
    primary_bytes: bytes
    #: Media type of the primary artifact.
    media_type: str
    #: Side files (e.g. Maccor MWF drive-cycle assets), each as ``(filename, bytes)``.
    assets: list[tuple[str, bytes]] = field(default_factory=list)

    def text(self, encoding: str = "utf-8") -> str:
        """Decode the primary artifact as text.

        Parameters
        ----------
        encoding : str, optional
            Encoding to decode with. Defaults to ``utf-8``.

        Returns
        -------
        str
            The decoded primary artifact.
        """
        return self.primary_bytes.decode(encoding)

    def save(self, directory: str | Path) -> list[Path]:
        """Write the primary artifact and any assets to ``directory``.

        Parameters
        ----------
        directory : str or Path
            Target directory. Created if it does not exist.

        Returns
        -------
        list[Path]
            Paths written, primary first followed by assets.
        """
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        primary_path = out / self.primary_filename
        primary_path.write_bytes(self.primary_bytes)
        written.append(primary_path)
        for name, data in self.assets:
            asset_path = out / name
            asset_path.write_bytes(data)
            written.append(asset_path)
        return written


def _as_text(response: Any) -> str:
    """Return the body of a plain-text endpoint response as a string.

    The transport parses JSON responses but hands back the raw ``requests``
    response for other content types, which is what the ``text/plain``
    protocol endpoints return.

    Parameters
    ----------
    response : Any
        Either a string already, or an object exposing ``.text``.

    Returns
    -------
    str
        The response body.
    """
    if isinstance(response, str):
        return response
    return getattr(response, "text", str(response))


def _as_protocol_config(protocol: str | dict) -> dict[str, Any]:
    """Normalise a protocol argument to the dict the API stores.

    Parameters
    ----------
    protocol : str or dict
        UCP as a YAML string or an already-parsed dict.

    Returns
    -------
    dict[str, Any]
        The parsed protocol.

    Raises
    ------
    ValueError
        If a string does not parse as YAML, or does not parse to a mapping.
    """
    if isinstance(protocol, dict):
        return protocol

    import yaml

    try:
        parsed = yaml.safe_load(protocol)
    except yaml.YAMLError as e:
        raise ValueError(f"protocol is not valid YAML: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(
            "protocol must parse to a mapping with a 'steps' key; got "
            f"{type(parsed).__name__}"
        )
    return parsed


class ProtocolClient:
    """Client for authoring, saving, and converting protocols.

    Covers the full lifecycle:

    - **author** — write UCP by hand, or :meth:`parse_file` a vendor
      protocol file
    - **check** — :meth:`validate`, :meth:`find_input_references`
    - **save** — :meth:`create` (or :meth:`create_or_get`) stores the protocol
      in a project; :meth:`list`, :meth:`get`, :meth:`update`, :meth:`delete`
      manage saved ones
    - **use** — pass the saved id to a simulation or a planned measurement
    - **export** — :meth:`convert` emits a vendor-native protocol file

    Saved protocols are project-scoped. Methods that need a project accept
    ``project_id``; when omitted it falls back to the ``project_id``
    configured on the parent :class:`~ionworks.Ionworks` client (resolved from
    ``IONWORKS_PROJECT_ID`` if not passed explicitly), and raise
    ``ValueError`` when no project_id is available from any source.
    """

    #: Base path for saved-protocol endpoints. Protocols are stored as
    #: experiment templates server-side; the SDK exposes them as protocols.
    _BASE = "/experiment_templates"

    def __init__(self, client: Any) -> None:
        """Initialize the ProtocolClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

    # --- Saved protocols ---------------------------------------------------

    def list(
        self,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        *,
        include: list[str] | None = None,
        name: str | None = None,
        name_exact: str | None = None,
        created_by_email: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        order_by: str | None = None,
        order: str | None = None,
    ) -> PaginatedList[Protocol]:
        """List a project's saved protocols.

        The response is lightweight by default: the protocol body and other
        large columns are omitted and come back as ``None``. Name them in
        ``include`` to fetch them, or call :meth:`get` for one full protocol.

        Filtering, ordering, and pagination are all applied by the database, so
        ``total`` reflects every protocol matching the filters rather than the
        size of the page returned.

        Parameters
        ----------
        project_id : str | None, optional
            Project whose protocols to list. Defaults to the project_id set on
            the Ionworks client.
        limit : int | None, optional
            Page size (1-100). When omitted the full list is returned.
        offset : int | None, optional
            Number of records to skip before the page starts.
        include : list[str] | None, optional
            Heavy columns to include. Any of :data:`INCLUDABLE_FIELDS`;
            unknown names are ignored by the API.
        name : str | None, optional
            Case-insensitive substring match on the protocol name.
        name_exact : str | None, optional
            Exact match on the protocol name. Takes precedence over ``name``.
        created_by_email : str | None, optional
            Case-insensitive substring match on the creator's email.
        created_after, created_before : str | None, optional
            ISO datetime bounds on when the protocol was saved.
        updated_after, updated_before : str | None, optional
            ISO datetime bounds on when the protocol was last changed.
        order_by : str | None, optional
            Column to sort by: ``name``, ``created_at``, or ``updated_at``.
        order : str | None, optional
            Sort direction, ``"asc"`` or ``"desc"``.

        Returns
        -------
        PaginatedList[Protocol]
            The matching protocols, with ``.count`` and ``.total``.
        """
        project_id = resolve_project_id(self.client, project_id)
        params: dict[str, str | int | float | bool | None] = {
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
            "include": ",".join(include) if include else None,
        }
        params.update(
            _build_filter_params(
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
        )
        endpoint = _build_endpoint(self._BASE, params)
        return _parse_list_response(self.client.get(endpoint), Protocol)

    def get(self, protocol_id: str) -> Protocol:
        """Get a saved protocol by id, including its full body.

        Parameters
        ----------
        protocol_id : str
            Id of the protocol to retrieve.

        Returns
        -------
        Protocol
            The protocol, with ``protocol_config`` and the other heavy columns
            populated.
        """
        return Protocol(**self.client.get(f"{self._BASE}/{protocol_id}"))

    def find_by_name(
        self,
        name: str,
        project_id: str | None = None,
    ) -> Protocol | None:
        """Find a saved protocol by exact name within a project.

        Parameters
        ----------
        name : str
            Exact protocol name to match.
        project_id : str | None, optional
            Project to search. Defaults to the project_id set on the Ionworks
            client.

        Returns
        -------
        Protocol | None
            The matching protocol (lightweight — call :meth:`get` for the
            body), or ``None`` when the project has no protocol by that name.

        Raises
        ------
        ValueError
            If more than one protocol in the project has this name. Names are
            not unique — protocols are deduplicated on their body, so two
            different protocols may share one — and picking arbitrarily
            between them would quietly simulate the wrong one. List them with
            ``list(name_exact=...)`` and select by id.
        """
        # Matched in the database rather than by scanning a full listing: an
        # unpaginated list is capped server-side, so a client-side scan would
        # return None for any protocol outside that first window.
        matches = self.list(project_id=project_id, name_exact=name, limit=2)
        if matches.total > 1:
            raise ValueError(
                f"{matches.total} protocols in this project are named "
                f"{name!r}. Protocol names are not unique; use "
                f"list(name_exact=...) and pick the one you want by id."
            )
        return matches[0] if matches else None

    def human_readable(self, protocol_id: str) -> str:
        """Render a saved protocol as human-readable text.

        Parameters
        ----------
        protocol_id : str
            Id of the protocol.

        Returns
        -------
        str
            The protocol described in prose, one line per step.
        """
        return _as_text(self.client.get(f"{self._BASE}/{protocol_id}/human_readable"))

    def source_protocol(self, protocol_id: str) -> str:
        """Return the original protocol text a saved protocol was created from.

        Parameters
        ----------
        protocol_id : str
            Id of the protocol.

        Returns
        -------
        str
            The source text as originally entered or parsed.

        Raises
        ------
        IonworksError
            404 when the protocol has no recorded source text (it was created
            directly from a UCP dict rather than from source).
        """
        return _as_text(self.client.get(f"{self._BASE}/{protocol_id}/source_protocol"))

    def create(
        self,
        name: str,
        protocol: str | dict,
        project_id: str | None = None,
        *,
        description: str | None = None,
        parameters_schema: dict[str, Any] | None = None,
        source_protocol: str | None = None,
        force_create: bool = False,
    ) -> Protocol:
        """Save a protocol to a project.

        By default this is content-addressed: saving a protocol whose body
        already exists in the project returns the existing row rather than a
        duplicate, so re-running a script is safe. Pass ``force_create=True``
        to always write a new row — useful for keeping two differently-named
        copies of the same protocol.

        Parameters
        ----------
        name : str
            Name for the protocol.
        protocol : str or dict
            The protocol as UCP YAML text or an already-parsed dict. A string
            is sent as ``source_protocol`` too unless one is given explicitly,
            so the original text is preserved.
        project_id : str | None, optional
            Project to save into. Defaults to the project_id set on the
            Ionworks client.
        description : str | None, optional
            Free-text description.
        parameters_schema : dict | None, optional
            Schema of the parameters the protocol leaves open. Defaults to
            ``{}`` (no open parameters).
        source_protocol : str | None, optional
            Original protocol text to record. Defaults to ``protocol`` when it
            is a string.
        force_create : bool, optional
            Write a new row even when an identical protocol already exists.
            Defaults to ``False``.

        Returns
        -------
        Protocol
            The saved protocol.

        Raises
        ------
        IonworksError
            409 when ``force_create`` is set and an identical protocol already
            exists — the content-hash uniqueness rule cannot be bypassed.
        """
        project_id = resolve_project_id(self.client, project_id)
        config = _as_protocol_config(protocol)
        if source_protocol is None and isinstance(protocol, str):
            source_protocol = protocol

        body: dict[str, Any] = {
            "name": name,
            "protocol_config": config,
            "parameters_schema": parameters_schema or {},
            "project_id": project_id,
            "force_create": force_create,
        }
        if description is not None:
            body["description"] = description
        if source_protocol is not None:
            body["source_protocol"] = source_protocol
        return Protocol(**self.client.post(self._BASE, body))

    def create_or_get(
        self,
        name: str,
        protocol: str | dict,
        project_id: str | None = None,
        **kwargs: Any,
    ) -> Protocol:
        """Save a protocol, returning the existing one if it is already saved.

        Thin alias for :meth:`create` with ``force_create=False``, named to
        match the create-or-get helpers on the other sub-clients. Matching is
        on protocol content, not on ``name``.

        Parameters
        ----------
        name : str
            Name for the protocol, used only when creating.
        protocol : str or dict
            The protocol as UCP YAML text or a parsed dict.
        project_id : str | None, optional
            Project to save into. Defaults to the client's project_id.
        **kwargs : Any
            Passed through to :meth:`create`.

        Returns
        -------
        Protocol
            The newly saved protocol, or the existing identical one.
        """
        return self.create(name, protocol, project_id, force_create=False, **kwargs)

    def update(
        self,
        protocol_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Protocol:
        """Rename a saved protocol or change its description.

        Only ``name`` and ``description`` are editable. A protocol's body is
        immutable — saved simulations reference it, so changing it in place
        would silently rewrite what they ran. Save a new protocol instead.

        Parameters
        ----------
        protocol_id : str
            Id of the protocol to update.
        name : str | None, optional
            New name. Left unchanged when omitted.
        description : str | None, optional
            New description. Left unchanged when omitted.

        Returns
        -------
        Protocol
            The updated protocol.
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return Protocol(**self.client.patch(f"{self._BASE}/{protocol_id}", body))

    def delete(self, protocol_id: str) -> None:
        """Delete a saved protocol.

        Parameters
        ----------
        protocol_id : str
            Id of the protocol to delete.
        """
        self.client.delete(f"{self._BASE}/{protocol_id}")

    # --- Authoring ---------------------------------------------------------

    def parse_file(self, file: str | Path | IO[bytes]) -> ParsedProtocol:
        """Parse a vendor protocol file into UCP.

        Accepts a cycler's own protocol file (Maccor, Arbin, Neware, Novonix,
        BioLogic, ...) and returns the equivalent UCP. Nothing is saved — pass
        the result's ``ucp`` to :meth:`create` to store it.

        Parameters
        ----------
        file : str, Path, or file object
            Path to the protocol file, or an already-open binary file object.

        Returns
        -------
        ParsedProtocol
            The UCP text plus a human-readable rendering and the names of any
            drive cycles or subroutines the file referenced but did not carry.
        """
        if isinstance(file, str | Path):
            path = Path(file)
            with path.open("rb") as handle:
                response = self.client.post_multipart(
                    "/protocols/parse",
                    files={"file": (path.name, handle, "application/octet-stream")},
                )
        else:
            filename = getattr(file, "name", "protocol")
            response = self.client.post_multipart(
                "/protocols/parse",
                files={
                    "file": (
                        Path(str(filename)).name,
                        file,
                        "application/octet-stream",
                    )
                },
            )
        return ParsedProtocol(**response)

    # --- Checking ----------------------------------------------------------

    def validate(self, protocol: str) -> dict[str, Any]:
        """Validate a UCP protocol string.

        Parameters
        ----------
        protocol : str
            The protocol YAML string to validate.

        Returns
        -------
        dict[str, Any]
            Validation result with ``valid`` (bool) and optionally ``error``
            (str) keys.
        """
        return self.client.post("/protocols/validate", {"protocol": protocol})

    def find_input_references(self, protocol: str) -> list[str]:
        """Find input references (external parameter placeholders) in a protocol.

        Parameters
        ----------
        protocol : str
            The protocol YAML string to inspect.

        Returns
        -------
        list[str]
            List of input reference names found in the protocol.
        """
        return self.client.post("/protocols/input_references", {"protocol": protocol})

    # --- Export ------------------------------------------------------------

    def convert(
        self,
        protocol: str | dict,
        target: ConversionTarget,
        drive_cycles: dict[str, Any] | None = None,
        filename_stem: str = "protocol",
        nominal_capacity_ah: float | None = None,
    ) -> ConvertResult:
        """Convert a UCP to a vendor-native protocol file.

        Returns the artifact as raw bytes. Use :meth:`ConvertResult.text` to
        decode as a string, or :meth:`ConvertResult.save` to write to disk.

        Parameters
        ----------
        protocol : str or dict
            UCP as a YAML string or dict.
        target : str
            One of ``maccor``, ``arbin``, ``neware``, ``biologic_bttest``,
            ``novonix``.
        drive_cycles : dict, optional
            Mapping of drive cycle name → samples, required when the protocol
            references DriveCycle steps.
        filename_stem : str, optional
            Stem for the returned primary filename. Defaults to ``protocol``.
        nominal_capacity_ah : float, optional
            Rated cell capacity in amp-hours. Required for ``neware`` when the
            protocol uses C-rate steps or cutoffs: Neware sets current in
            absolute mA and has no C-rate mode, so the rate cannot be resolved
            without it. Other targets express C-rate natively and ignore it.

        Returns
        -------
        ConvertResult
            Holds the primary artifact bytes plus any side assets.
        """
        body: dict[str, Any] = {
            "protocol": protocol,
            "target": target,
            "filename_stem": filename_stem,
        }
        if drive_cycles is not None:
            body["drive_cycles"] = drive_cycles
        if nominal_capacity_ah is not None:
            body["nominal_capacity_ah"] = nominal_capacity_ah
        response = self.client.post("/protocols/convert", body)
        primary = response["primary"]
        return ConvertResult(
            target=response["target"],
            primary_filename=primary["filename"],
            primary_bytes=base64.b64decode(primary["content_base64"]),
            media_type=primary["media_type"],
            assets=[
                (a["filename"], base64.b64decode(a["content_base64"]))
                for a in response.get("assets", [])
            ],
        )
