"""Shared helpers for signed-URL uploads used by multiple SDK clients."""

from __future__ import annotations

from datetime import UTC
import io
import os
import threading

import requests

from .errors import IonworksError

#: (connect, read) timeout for signed-URL uploads. Read is generous because
#: uploaded files (measurements, raw cycler dumps) can be large.
UPLOAD_TIMEOUT: tuple[float, float] = (10, 300)

#: Bodies at or above this size use S3 multipart instead of a single PUT.
#: Chosen well below the observed 1.2-1.6 GB single-PUT cliff.
UPLOAD_MULTIPART_THRESHOLD_BYTES: int = 512 * 1024 * 1024

#: Largest body still worth handing to the single PUT when multipart is
#: unavailable. Deliberately the conservative end of the observed 1.2-1.6 GB
#: cliff: the fallback exists to preserve uploads that worked before multipart
#: existed, so it must not hand the PUT path a body it is likely to drop.
UPLOAD_SINGLE_PUT_MAX_BYTES: int = 1024 * 1024 * 1024

#: Assumed throughput for the pre-flight credential-expiry check, from the
#: serial measurement on 2026-08-09. Deliberately conservative: multipart is
#: faster, so the check errs toward warning about uploads that would have
#: succeeded. Revisit once multipart throughput is measured.
UPLOAD_ASSUMED_THROUGHPUT_BYTES_PER_SEC: int = 9 * 1024 * 1024

#: Multipart part size, matching the backend's proven TransferConfig.
UPLOAD_CHUNK_BYTES: int = 64 * 1024 * 1024


class _MultipartUnavailable(IonworksError):
    """The multipart path cannot run here at all, as distinct from having failed.

    Separates "this build or environment has no working multipart" -- boto3 not
    installed, or storage refusing the S3 credentials outright -- from "the
    transfer started and broke". Only the former is worth retrying as a single
    PUT: a genuine transfer failure would fail the same way again, more slowly.

    Subclasses ``IonworksError`` so that when it does surface (a body too large
    for the PUT fallback) it still matches the SDK's documented contract.
    """


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


def upload_file(
    target,
    body,
    size_bytes: int,
    *,
    timeout: tuple[float, float] | float | None = None,
    content_type: str = "application/octet-stream",
    max_concurrency: int | None = None,
) -> None:
    """Upload a body to storage, choosing single-PUT or multipart by size.

    Parameters
    ----------
    target : UploadInfo
        Upload target from an ``initiate-upload`` response.
    body : IO[bytes] | bytes
        File handle (streamed) or buffered bytes. Only a seekable handle can
        take the multipart path -- ``upload_fileobj`` needs ``.read()``.
    size_bytes : int
        Body length, used for the threshold decision.
    timeout : tuple[float, float] | float | None, optional
        Overrides the module default. On the multipart path this is per part.
    content_type : str, optional
        MIME type for the stored object.
    max_concurrency : int | None, optional
        Caps multipart connections. Callers already running their own thread
        pool pass a reduced value so the product stays bounded.

    Raises
    ------
    IonworksError
        If the upload fails, or if multipart is unavailable for a body too
        large for the single-PUT fallback to carry.

    Notes
    -----
    When multipart is unavailable rather than merely broken -- boto3 missing
    (it ships only in the ``large-uploads`` extra) or storage refusing the S3
    credentials, as a local single-tenant Supabase does -- a body at or under
    ``UPLOAD_SINGLE_PUT_MAX_BYTES`` falls back to the single PUT. Without that,
    adding multipart would *regress* the 512 MiB-1 GiB band, which uploaded
    fine before this path existed.
    """
    can_multipart = (
        size_bytes >= UPLOAD_MULTIPART_THRESHOLD_BYTES
        and target.multipart is not None
        and hasattr(body, "read")
        and getattr(body, "seekable", lambda: False)()
    )
    if can_multipart:
        # Remember where the body started rather than assuming 0: a
        # caller-supplied handle may be positioned mid-file, and the fallback
        # has to re-send from exactly the same offset the size was measured at.
        start = body.tell()
        try:
            _upload_multipart(
                target.multipart,
                body,
                size_bytes,
                content_type=content_type,
                timeout=timeout,
                max_concurrency=max_concurrency,
            )
            return
        except _MultipartUnavailable:
            if size_bytes > UPLOAD_SINGLE_PUT_MAX_BYTES:
                raise
            body.seek(start)
    upload_to_signed_url(
        target.signed_url,
        body,
        timeout=timeout if timeout is not None else UPLOAD_TIMEOUT,
        content_type=content_type,
    )


def _require_boto3():
    """Import boto3 lazily, or raise a message naming the extra.

    Returns every boto3/botocore symbol the multipart path needs, so no other
    function imports them and no ImportError can escape ungracefully.

    Returns
    -------
    tuple
        ``(boto3, BotoConfig, TransferConfig, transfer_exceptions)`` where the
        last element is a tuple of exception classes worth reporting as upload
        failures rather than programming errors.

    Raises
    ------
    _MultipartUnavailable
        If boto3 is not installed, with the extra named in the message.

    Notes
    -----
    ``BotoCoreError`` -- not ``EndpointConnectionError`` -- is the base that
    actually covers the network failures this module exists to translate.
    ``SSLError``, ``ConnectionClosedError``, ``ReadTimeoutError`` and
    ``ConnectTimeoutError`` all derive from ``ConnectionError``/
    ``HTTPClientError`` under ``BotoCoreError``, and none of them is an
    ``EndpointConnectionError``; catching the narrow class would let the bare
    SSL error this feature was built to replace escape untranslated.

    ``S3UploadFailedError`` is live only on the ``upload_file(Filename=...)``
    path, which wraps ``ClientError`` in it for backwards compatibility;
    ``upload_fileobj`` re-raises the underlying botocore exception untouched.
    """
    try:
        import boto3
        from boto3.exceptions import S3UploadFailedError
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        raise _MultipartUnavailable(
            "Uploading a file this large requires the large-upload extra. "
            "Install it with: pip install 'ionworks-api[large-uploads]'"
        ) from None
    return (
        boto3,
        BotoConfig,
        TransferConfig,
        (ClientError, BotoCoreError, S3UploadFailedError),
    )


def _split_timeout(
    timeout: tuple[float, float] | float | None,
) -> tuple[float, float]:
    """Normalize a caller timeout into botocore's (connect, read) pair.

    On the multipart path these apply **per part**, not to the whole transfer,
    which is what stops the read timeout acting as an implicit cap on file size.

    Parameters
    ----------
    timeout : tuple[float, float] | float | None
        A ``(connect, read)`` pair, a single value used for both, or ``None``
        to fall back to the module-level ``UPLOAD_TIMEOUT``.

    Returns
    -------
    tuple[float, float]
        The ``(connect, read)`` timeouts in seconds.
    """
    if timeout is None:
        timeout = UPLOAD_TIMEOUT
    if isinstance(timeout, (int, float)):
        return float(timeout), float(timeout)
    connect, read = timeout
    return float(connect), float(read)


def _make_s3_client(mp, boto3, BotoConfig, timeout=None):
    """Build a boto3 S3 client for a multipart target. Separated for testing.

    Parameters
    ----------
    mp : MultipartUploadInfo
        Endpoint, region, and temporary credentials from the backend.
    boto3 : module
        The boto3 module, supplied by ``_require_boto3``.
    BotoConfig : type
        ``botocore.config.Config``, supplied by ``_require_boto3``.
    timeout : tuple[float, float] | float | None, optional
        Caller timeout, applied per part. Defaults to ``UPLOAD_TIMEOUT``.

    Returns
    -------
    Any
        A configured boto3 S3 client.
    """
    connect_timeout, read_timeout = _split_timeout(timeout)
    return boto3.client(
        "s3",
        endpoint_url=_normalize_url(mp.endpoint),
        region_name=mp.region,
        aws_access_key_id=mp.access_key_id,
        aws_secret_access_key=mp.secret_access_key,
        aws_session_token=mp.session_token,
        config=BotoConfig(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "adaptive"},
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        ),
    )


def _check_credential_lifetime(mp, size_bytes: int) -> None:
    """Warn when the transfer looks likely to outlive its credential.

    This **warns rather than raises** for a transfer that merely looks too
    slow. The estimate uses a deliberately pessimistic serial throughput, so
    turning it into a hard gate would refuse uploads that multipart's parallel
    connections would in fact complete: at 9 MB/s against a ~1 hour token it
    caps out near 31 GB, far below the 500 GB the S3 path actually supports,
    and re-running only mints another equally short-lived credential — there is
    no action the caller could take to get past it.

    An already-expired credential is different: that upload cannot succeed, and
    a fresh one is exactly one re-run away, so it still raises.

    Parameters
    ----------
    mp : MultipartUploadInfo
        Upload target; ``expires_at`` is optional and the check is skipped when
        the backend does not supply it.
    size_bytes : int
        Body length, converted to an estimated duration at the module's assumed
        throughput.

    Raises
    ------
    IonworksError
        If the credential has already expired.

    Warns
    -----
    UserWarning
        If the estimated transfer time exceeds the credential's remaining life.
    """
    if not mp.expires_at:
        return
    from datetime import datetime

    # `expires_at` comes from the server, whose version this client is not
    # locked to. An unparseable or offset-naive value means "unknown expiry" —
    # skip the check rather than crashing a multi-gigabyte upload with a raw
    # TypeError/ValueError, which is what every other path here avoids.
    try:
        expires = datetime.fromisoformat(mp.expires_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        remaining = (expires - datetime.now(UTC)).total_seconds()
    except (ValueError, TypeError):
        return

    if remaining <= 0:
        raise IonworksError(
            "Upload credentials have already expired. Re-run the upload to "
            "obtain fresh credentials."
        )

    needed = size_bytes / UPLOAD_ASSUMED_THROUGHPUT_BYTES_PER_SEC
    if remaining < needed:
        import warnings

        warnings.warn(
            f"Upload credentials expire in {remaining / 60:.0f} min and this "
            f"{size_bytes / 1e9:.2f} GB upload could take around "
            f"{needed / 60:.0f} min at a conservative estimate. Multipart "
            f"usually runs faster than that, so this may well succeed — but if "
            f"it fails partway through with an authorization error, that is why.",
            UserWarning,
            stacklevel=3,
        )


#: S3 error codes meaning "these credentials will never work here", as opposed
#: to a transfer that broke partway. A local single-tenant Supabase answers
#: every user JWT with ``InvalidAccessKeyId`` because its storage container
#: supports only a static key pair and no session token; the rest cover a
#: credential that is malformed, unsigned, or expired server-side.
_CREDENTIAL_REJECTION_CODES = frozenset(
    {
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
        "TokenRefreshRequired",
    }
)


def _is_credential_rejection(exc) -> bool:
    """True when storage refused the credentials outright.

    Parameters
    ----------
    exc : Exception
        The exception raised by the transfer.

    Returns
    -------
    bool
        ``True`` if the failure means multipart cannot work here at all.

    Notes
    -----
    A ``ClientError`` carries a structured code. The ``Filename`` path wraps
    it in ``S3UploadFailedError``, which keeps only the string form, so that
    case has to be matched textually.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return code in _CREDENTIAL_REJECTION_CODES
    text = str(exc)
    return any(code in text for code in _CREDENTIAL_REJECTION_CODES)


def _filesystem_path(body, size_bytes: int) -> str | None:
    """Return the real file path behind ``body``, when streaming from one is safe.

    Parameters
    ----------
    body : IO[bytes]
        The upload body.
    size_bytes : int
        Length the caller measured and will record, used to confirm the path on
        disk holds exactly the bytes being uploaded.

    Returns
    -------
    str | None
        The path, or ``None`` when the body is not a whole real file.

    Notes
    -----
    s3transfer buffers each part of a *file object* into memory:
    ``UploadSeekableInputManager`` reads every part into a fresh ``BytesIO``,
    bounded only by ``max_in_memory_upload_chunks`` (10), which boto3's
    ``TransferConfig`` does not expose -- about 640 MB resident at a 64 MiB
    part size. Given a filename it uses deferred per-part file handles and
    streams instead, which is what the backend's own uploader does.

    The offset and on-disk size are both checked because ``Filename`` always
    sends the whole file from byte 0: a handle positioned mid-file, or one
    whose file changed size, would silently upload different bytes than the
    ``size_bytes`` recorded on the record.
    """
    name = getattr(body, "name", None)
    if not isinstance(name, str):
        return None
    try:
        if body.tell() != 0:
            return None
        if not os.path.isfile(name) or os.path.getsize(name) != size_bytes:
            return None
    except (OSError, ValueError):
        return None
    return name


def _upload_multipart(
    mp, body, size_bytes, *, content_type, timeout, max_concurrency=None
) -> None:
    """Upload via S3 multipart.

    Retries individual parts within this call via botocore's adaptive retry
    mode. It does NOT persist the UploadId across calls: a failed transfer is
    aborted, and re-running restarts from zero.

    Parameters
    ----------
    mp : MultipartUploadInfo
        Endpoint, bucket, object path, and temporary credentials.
    body : IO[bytes]
        Seekable binary handle, streamed part by part. A handle backed by a
        whole real file is uploaded by path instead, which avoids s3transfer
        holding parts in memory -- see ``_filesystem_path``.
    size_bytes : int
        Body length, used for the credential-lifetime check and error messages.
    content_type : str
        MIME type stored with the object.
    timeout : tuple[float, float] | float | None
        ``(connect, read)`` timeouts applied **per part** rather than to the
        whole transfer, so a generous read timeout no longer caps file size.
        Defaults to ``UPLOAD_TIMEOUT``.
    max_concurrency : int | None, optional
        Caps parallel part uploads. Defaults to 10.

    Raises
    ------
    _MultipartUnavailable
        If boto3 is missing or storage refuses the S3 credentials outright, so
        the caller can decide whether a single PUT is still worth trying.
    IonworksError
        If the credentials have already expired, or the transfer fails; the
        message reports bytes sent out of the total.
    """
    boto3, BotoConfig, TransferConfig, transfer_errors = _require_boto3()
    _check_credential_lifetime(mp, size_bytes)
    s3 = _make_s3_client(mp, boto3, BotoConfig, timeout=timeout)

    # s3transfer fires the callback from every worker thread, so `sent` is
    # shared mutable state and `sent += n` is a non-atomic read-modify-write.
    # CPython's GIL makes a lost update unobservable in practice today (it
    # could not be reproduced even at a 1 ns switch interval), so this guards
    # correctness rather than a reproducible bug — and a free-threaded build
    # removes the accident that currently saves it.
    sent = 0
    sent_lock = threading.Lock()

    def _progress(n: int) -> None:
        nonlocal sent
        with sent_lock:
            sent += n

    # multipart_threshold is set for symmetry with the backend's config;
    # upload_file already gates at 512 MiB, so it is never the deciding factor.
    config = TransferConfig(
        multipart_threshold=UPLOAD_CHUNK_BYTES,
        multipart_chunksize=UPLOAD_CHUNK_BYTES,
        max_concurrency=max_concurrency or 10,
    )
    common = {
        "Bucket": mp.bucket,
        "Key": mp.object_path,
        "ExtraArgs": {"ContentType": content_type},
        "Config": config,
        "Callback": _progress,
    }
    path = _filesystem_path(body, size_bytes)
    try:
        if path is not None:
            # Streams per-part from the file instead of buffering parts in RAM.
            s3.upload_file(Filename=path, **common)
        else:
            s3.upload_fileobj(Fileobj=body, **common)
    except transfer_errors as e:
        if _is_credential_rejection(e):
            raise _MultipartUnavailable(
                f"Storage refused the multipart upload credentials. ({e})"
            ) from None
        # `sent` is approximate: a part that fails and is retried reports its
        # bytes on each attempt, so this can exceed the file size.
        raise IonworksError(
            f"Upload failed after transferring about {sent / 1e9:.2f} GB of "
            f"{size_bytes / 1e9:.2f} GB. Files this large upload via S3 "
            f"multipart, so a failure here is usually the network or an "
            f"expired session rather than the file size. Retry, or check the "
            f"project's storage quota. ({e})"
        ) from None
