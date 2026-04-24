"""Protocol client for validating and inspecting battery test protocols.

This module provides the :class:`ProtocolClient` for validating Universal
Cycler Protocol (UCP) YAML strings and finding input references within them.
"""

from __future__ import annotations

from typing import Any


class ProtocolClient:
    """Client for protocol validation and inspection.

    Provides methods to validate UCP protocol strings and discover input
    references (external parameter placeholders) within protocols.
    """

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
