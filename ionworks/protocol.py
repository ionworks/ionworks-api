"""Protocol client for validating, inspecting, and converting UCP protocols.

Provides :class:`ProtocolClient` for validating UCP YAML strings, finding
input references, and converting a UCP to a vendor-native protocol file
(Maccor, Arbin, Neware, BioLogic BT-Test, Novonix).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ConversionTarget = Literal["maccor", "arbin", "neware", "biologic_bttest", "novonix"]


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


class ProtocolClient:
    """Client for protocol validation, inspection, and conversion."""

    def __init__(self, client: Any) -> None:
        """Initialize the ProtocolClient.

        Parameters
        ----------
        client : Any
            The HTTP client instance for making API requests.
        """
        self.client = client

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
