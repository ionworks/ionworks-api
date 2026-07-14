"""Client for raw-data records (original uploaded files, org + project scoped)."""

from __future__ import annotations

import json
import os
from typing import IO, Any

from .cache import _load_from_cache, _save_to_cache
from .models import PaginatedList, RawData, _build_endpoint, _parse_list_response

#: Default read timeout (seconds) for raw-file downloads. Generous because raw
#: files are original uploads that can be large; the connect timeout stays short.
_DEFAULT_DOWNLOAD_READ_TIMEOUT = 300.0


class RawDataClient:
    """Upload and manage raw-data files and their measurement links.

    Access via ``client.raw_data``. Raw-data records are original uploaded
    files stored as-is, scoped to an organization and a project.
    """

    _BASE = "/raw_data"

    def __init__(self, client: Any) -> None:
        """Initialize the RawDataClient.

        Parameters
        ----------
        client : Any
            The parent :class:`~ionworks.client.Ionworks` instance.
        """
        self.client = client

    def upload(
        self,
        project_id: str,
        file: str | os.PathLike | IO[bytes],
        name: str | None = None,
        source: str | None = None,
        metadata: dict | None = None,
    ) -> RawData:
        """Upload a raw file, stored as-is under the given project.

        Parameters
        ----------
        project_id : str
            Owning project ID.
        file : str | os.PathLike | IO[bytes]
            Path to a file, or an open binary file-like object.
        name : str | None, optional
            Label for the record. Defaults to the file's basename.
        source : str | None, optional
            Free-text provenance for where the raw file came from (a URL, DOI,
            S3 URI, vendor/cycler export description, etc.).
        metadata : dict | None, optional
            Free-form metadata stored alongside the record.

        Returns
        -------
        RawData
            The created raw-data record.
        """
        handle, filename, opened = _resolve_file(file)
        try:
            data = {
                "project_id": project_id,
                "name": name or filename,
                "metadata": json.dumps(metadata or {}),
            }
            if source is not None:
                data["source"] = source
            files = {"file": (filename, handle, "application/octet-stream")}
            response = self.client.upload_multipart(self._BASE, data=data, files=files)
        finally:
            if opened:
                handle.close()
        if not isinstance(response, dict):
            raise TypeError("Unexpected non-dict response from /raw_data upload")
        return RawData(**response)

    def list(
        self, project_id: str, limit: int = 100, offset: int = 0
    ) -> PaginatedList[RawData]:
        """List raw-data records for a project.

        Parameters
        ----------
        project_id : str
            Project whose raw-data records should be listed.
        limit : int, optional
            Maximum number of records to return. Defaults to 100.
        offset : int, optional
            Number of records to skip for pagination. Defaults to 0.

        Returns
        -------
        PaginatedList[RawData]
            A list-like page of records with ``.count`` and ``.total``.
        """
        endpoint = _build_endpoint(
            self._BASE, {"project_id": project_id, "limit": limit, "offset": offset}
        )
        return _parse_list_response(self.client.get(endpoint), RawData)

    def get(self, raw_data_id: str) -> RawData:
        """Fetch one raw-data record.

        Parameters
        ----------
        raw_data_id : str
            ID of the raw-data record to retrieve.

        Returns
        -------
        RawData
            The requested record.
        """
        return RawData(**self.client.get(f"{self._BASE}/{raw_data_id}"))

    def download_url(self, raw_data_id: str) -> str:
        """Get a short-lived signed download URL for the stored file.

        Parameters
        ----------
        raw_data_id : str
            ID of the raw-data record.

        Returns
        -------
        str
            A signed URL that can be used to download the file directly.
        """
        return self.client.get(f"{self._BASE}/{raw_data_id}/download-url")["url"]

    def download(
        self,
        raw_data_id: str,
        use_cache: bool = True,
        read_timeout: float = _DEFAULT_DOWNLOAD_READ_TIMEOUT,
    ) -> bytes:
        """Download the stored raw file's bytes, with a local disk cache.

        This is the method to use inside processing scripts so the same script
        runs for any collaborator: it pulls the original file from shared
        storage rather than assuming a local path only you have. Raw-data files
        are immutable (provenance), so the cache never goes stale for a given
        id — the first call downloads via a short-lived signed URL and stores
        the bytes under the shared Ionworks cache directory
        (``~/.ionworksdata_cache`` by default); subsequent calls read from disk.

        The cache is the same one used for measurement downloads and is
        configured with the module-level ``set_cache_enabled`` /
        ``set_cache_directory`` / ``set_cache_ttl`` / ``clear_cache`` helpers
        exported from :mod:`ionworks`.

        Parameters
        ----------
        raw_data_id : str
            ID of the raw-data record to download.
        use_cache : bool, optional
            If True (default), read from the local cache when present and store
            the downloaded bytes for future calls. Set to False to force a
            fresh download and bypass the cache entirely.
        read_timeout : float, optional
            Read timeout in seconds for the file-body download. Defaults to
            300s — much longer than the client's general request timeout
            because raw files are original uploads and can be large; a big
            cycler export would otherwise abort mid-download on the default
            ~10s read timeout. The connection timeout stays short.

        Returns
        -------
        bytes
            The raw file content, byte-for-byte as uploaded.
        """
        if use_cache:
            cached = _load_from_cache(raw_data_id)
            if cached is not None and "files" in cached and cached["files"]:
                # A raw-data record holds exactly one file; return its bytes.
                return next(iter(cached["files"].values()))

        record = self.get(raw_data_id)
        url = self.download_url(raw_data_id)
        # (connect, read) tuple: keep the connect timeout short, but allow a
        # long read so a large file body does not time out mid-transfer.
        connect_timeout = min(self.client.request_timeout, 10)
        content = self.client.session.get(url, timeout=(connect_timeout, read_timeout))
        content.raise_for_status()
        data = content.content

        if use_cache:
            _save_to_cache(raw_data_id, {"files": {record.filename: data}})

        return data

    def update(
        self,
        raw_data_id: str,
        name: str | None = None,
        source: str | None = None,
        metadata: dict | None = None,
    ) -> RawData:
        """Partial-update a record's name, source, and/or metadata.

        Only the fields that are provided (non-``None``) are sent.

        Parameters
        ----------
        raw_data_id : str
            ID of the raw-data record to update.
        name : str | None, optional
            New label for the record.
        source : str | None, optional
            New free-text provenance. Pass an empty string to clear it.
        metadata : dict | None, optional
            New free-form metadata (replaces the existing metadata).

        Returns
        -------
        RawData
            The updated record.
        """
        payload: dict = {}
        if name is not None:
            payload["name"] = name
        if source is not None:
            payload["source"] = source
        if metadata is not None:
            payload["metadata"] = metadata
        return RawData(**self.client.patch(f"{self._BASE}/{raw_data_id}", payload))

    def delete(self, raw_data_id: str) -> None:
        """Delete a raw-data record and its stored file.

        Parameters
        ----------
        raw_data_id : str
            ID of the raw-data record to delete.
        """
        self.client.delete(f"{self._BASE}/{raw_data_id}")

    def list_measurements(
        self, raw_data_id: str, limit: int = 100, offset: int = 0
    ) -> PaginatedList[str]:
        """List measurement IDs produced from this raw-data record.

        The endpoint returns plain string ids, so the :class:`PaginatedList`
        is built directly — :func:`_parse_list_response` cannot be used here
        because it calls ``model_class(**item)`` per item, which would crash
        on plain strings.

        Parameters
        ----------
        raw_data_id : str
            ID of the raw-data record.
        limit : int, optional
            Maximum number of ids to return. Defaults to 100.
        offset : int, optional
            Number of ids to skip for pagination. Defaults to 0.

        Returns
        -------
        PaginatedList[str]
            A list-like page of measurement id strings with ``.count`` and
            ``.total``.
        """
        endpoint = _build_endpoint(
            f"{self._BASE}/{raw_data_id}/cell_measurements",
            {"limit": limit, "offset": offset},
        )
        resp = self.client.get(endpoint)
        return PaginatedList(
            items=list(resp.get("items", [])),
            total=resp.get("total", 0),
        )


def _resolve_file(
    file: str | os.PathLike | IO[bytes],
) -> tuple[IO[bytes], str, bool]:
    """Return an open binary handle, a filename, and whether we opened it.

    Parameters
    ----------
    file : str | os.PathLike | IO[bytes]
        A filesystem path or an already-open binary file-like object.

    Returns
    -------
    tuple[IO[bytes], str, bool]
        The open binary handle, a filename derived from the path or the
        file-like object's ``name``, and a boolean that is ``True`` only when
        this function opened the handle. The caller closes the handle in a
        ``finally`` only when that boolean is ``True`` — a caller-owned
        file-like object is never closed here.
    """
    if isinstance(file, (str, os.PathLike)):
        return open(file, "rb"), os.path.basename(str(file)), True
    filename = os.path.basename(getattr(file, "name", "upload.bin"))
    return file, filename, False
