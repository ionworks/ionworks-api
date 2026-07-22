"""
Custom exception classes for the Ionworks API client.

This module defines :class:`IonworksError`, which is raised when API requests
fail or return error responses.
"""

from typing import Any


class IonworksError(Exception):
    """Custom exception for Ionworks API errors.

    Attributes
    ----------
    message : str
        A string description of the error.
    data : dict[str, Any] | None
        Structured error data if available (e.g., from API error response).
    status_code : int | None
        HTTP status code if applicable.
    error_code : str | None
        Machine-readable error code from the server (e.g., ``"NOT_FOUND"``).
    """

    def __init__(
        self,
        message: str | dict[str, Any],
        status_code: int | None = None,
    ) -> None:
        """Initialize the IonworksError.

        Parameters
        ----------
        message : str | dict[str, Any]
            Error message string or dict containing error details.
            Supports both the legacy ``{"detail": ...}`` format and the new
            standardized ``{"error_code": ..., "message": ..., "detail": ...}``
            format.
        status_code : int | None
            Optional HTTP status code.
        """
        self.status_code = status_code
        self.error_code: str | None = None

        # Parse message into string, optional data dict, and error_code
        if isinstance(message, dict):
            self.error_code = message.get("error_code")
            self.message = message.get("message", str(message))
            self.data: dict[str, Any] | None = message
        else:
            self.message = message
            self.data = None

        super().__init__(self.message)

    def __str__(self) -> str:
        """Return string representation of the error."""
        return f"error code: {self.status_code}, message: {self.message}"
