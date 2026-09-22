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


class MeasurementProcessingError(IonworksError):
    """Raised when a measurement upload could not be processed by the server.

    Uploading a ``time_series`` measurement is a two-part operation: the
    request that creates the record returns as soon as the file is stored, and
    the steps are derived afterwards on the server. A file the server cannot
    read — a missing or unreadable Step column, say — is therefore rejected
    *after* the create call has already returned successfully.

    The upload methods wait for that second part and raise this rather than
    returning a measurement that looks created but holds no usable data.

    A batch wait is not fail-fast, so one of these can report several
    measurements at once — ``failures`` holds every one of them.

    Attributes
    ----------
    failures : dict[str, str]
        Reason keyed by the id of each measurement whose processing failed.
        The records exist and can be inspected or deleted; they simply have
        no steps.
    """

    def __init__(self, message: str, failures: dict[str, str] | None = None) -> None:
        """Initialize the error.

        Parameters
        ----------
        message : str
            Why processing failed. For a batch, a summary naming each failure.
        failures : dict[str, str], optional
            Reason keyed by failed measurement id.
        """
        super().__init__(message)
        self.failures = failures or {}

    @property
    def measurement_id(self) -> str | None:
        """The first — and, for a single upload, only — failed measurement id.

        None if no failure was recorded. Read ``failures`` when waiting on a
        batch, where this reports only one of several.
        """
        return next(iter(self.failures), None)

    def __str__(self) -> str:
        """Return string representation of the error."""
        if len(self.failures) == 1:
            return f"measurement {self.measurement_id}: {self.message}"
        return self.message
