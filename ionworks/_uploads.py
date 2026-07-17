"""Shared helpers for signed-URL uploads used by multiple SDK clients."""

from __future__ import annotations

import io

import requests

from .errors import IonworksError

#: (connect, read) timeout for signed-URL uploads. Read is generous because
#: uploaded files (measurements, raw cycler dumps) can be large.
UPLOAD_TIMEOUT: tuple[float, float] = (10, 300)


def _normalize_url(url: str) -> str:
    """Replace Docker-internal hostname with localhost for local development."""
    return url.replace("host.docker.internal", "localhost")


def upload_to_signed_url(
    signed_url: str,
    data: bytes | io.IOBase,
    timeout: tuple[float, float] | float = UPLOAD_TIMEOUT,
    content_type: str = "application/octet-stream",
) -> None:
    """PUT data to a signed URL. Raises IonworksError on failure.

    Parameters
    ----------
    signed_url : str
        The signed URL to PUT the data to. Docker-internal hostnames are
        rewritten to localhost for local development.
    data : bytes | io.IOBase
        The file content as bytes or an open binary file handle.
    timeout : tuple[float, float] | float, optional
        Request timeout. Defaults to the module-level ``UPLOAD_TIMEOUT``
        ``(connect, read)`` tuple with a generous 300s read for large files.
    content_type : str, optional
        MIME type for the upload. Defaults to "application/octet-stream".

    Raises
    ------
    IonworksError
        If the upload request fails.
    """
    url = _normalize_url(signed_url)
    try:
        response = requests.put(
            url, data=data, headers={"Content-Type": content_type}, timeout=timeout
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise IonworksError(f"Failed to upload to signed URL: {e}") from None
