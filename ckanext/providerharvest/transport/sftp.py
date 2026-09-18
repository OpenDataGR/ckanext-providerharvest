"""Phase 1.5 transport: SFTP, for whole-file (``delivery_mode=bulk_file``)
provider sources.

Security properties this transport is responsible for maintaining:
  * The network target (host:port) is (re-)validated via
    ``logic.validators`` on every ``connect()`` -- same requirement and
    same reasoning as the HTTP transport.
  * The server's host key is pinned at registration time (see
    ``fetch_host_key_fingerprint``, used by the
    ``provider_source_fetch_host_key`` action) and verified on every
    connection: SSH has no CA hierarchy, so trust-on-first-use-with-
    pinning is the standard safe pattern -- the SSH analogue of TLS
    chain validation. There is deliberately no "skip host key check"
    option; a mismatch always raises.
  * Files are listed (path/mtime/size only, never bytes) and opened as
    streamed, seekable-free handles -- callers must not read a whole
    file into memory, since these can be multi-GB provider exports.
  * Every connection attempt is reported via ``on_request`` for audit
    logging, reusing the same ``OutboundRequestEvent`` shape the HTTP
    transport uses -- never credentials.
"""

from __future__ import annotations

import fnmatch
import hashlib
import stat
import time
from typing import BinaryIO, Callable, Optional

import paramiko

from ckanext.providerharvest.audit import OutboundRequestEvent
from ckanext.providerharvest.logic.validators import assert_safe_network_target
from ckanext.providerharvest.secrets.base import SecretBundle
from ckanext.providerharvest.transport.base import Entry, Page, Transport

DEFAULT_PORT = 22
DEFAULT_TIMEOUT_S = 30


class HostKeyMismatchError(Exception):
    """The server's current host key doesn't match the one pinned at
    registration time -- either the server changed keys for a real reason
    (in which case a human needs to re-confirm and re-pin, not this code
    silently accepting it), or something is impersonating it."""


def _fingerprint(key: paramiko.PKey) -> str:
    """SHA256 fingerprint, base64-encoded -- matches modern
    ``ssh-keygen -E sha256`` output, which is what a provider would see
    and be asked to confirm against, not paramiko's legacy MD5 default."""
    import base64
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def fetch_host_key_fingerprint(
    host: str, port: int = DEFAULT_PORT, *, allow_private_ranges: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    transport_factory: Callable[[tuple], "paramiko.Transport"] = paramiko.Transport,
) -> str:
    """Connect just far enough to read the server's host key, without
    authenticating. Used both by SFTPTransport's own pinning check and by
    the ``provider_source_fetch_host_key`` action, so a provider can be
    shown the real fingerprint to confirm before it's pinned."""
    assert_safe_network_target(host, port, allow_private_ranges=allow_private_ranges)
    transport = transport_factory((host, port))
    try:
        transport.start_client(timeout=timeout_s)
        key = transport.get_remote_server_key()
        if key is None:
            raise HostKeyMismatchError("Server did not present a host key")
        return _fingerprint(key)
    finally:
        transport.close()


class SFTPTransport(Transport):
    def __init__(
        self,
        host: str,
        secret: SecretBundle,
        *,
        port: int = DEFAULT_PORT,
        remote_path: str = "/",
        glob_pattern: str = "*",
        pinned_host_key_fingerprint: Optional[str] = None,
        allow_private_ranges: bool = False,
        connect_timeout_s: int = DEFAULT_TIMEOUT_S,
        on_request: Optional[Callable[[OutboundRequestEvent], None]] = None,
        harvest_source_id: Optional[str] = None,
        harvest_job_id: Optional[str] = None,
        transport_factory: Callable[[tuple], "paramiko.Transport"] = paramiko.Transport,
        sftp_client_factory: Callable[["paramiko.Transport"], "paramiko.SFTPClient"] = (
            paramiko.SFTPClient.from_transport
        ),
    ):
        self._host = host
        self._port = port
        self._secret = secret
        self._remote_path = remote_path
        self._glob_pattern = glob_pattern
        self._pinned_host_key_fingerprint = pinned_host_key_fingerprint
        self._allow_private_ranges = allow_private_ranges
        self._connect_timeout_s = connect_timeout_s
        self._on_request = on_request
        self._harvest_source_id = harvest_source_id
        self._harvest_job_id = harvest_job_id
        self._transport_factory = transport_factory
        self._sftp_client_factory = sftp_client_factory
        self._transport: Optional["paramiko.Transport"] = None
        self._sftp: Optional["paramiko.SFTPClient"] = None

    def _report(self, method: str, path: str, *, status: Optional[int],
                duration_ms: float, error: Optional[str] = None) -> None:
        if self._on_request is None:
            return
        self._on_request(OutboundRequestEvent(
            harvest_source_id=self._harvest_source_id,
            harvest_job_id=self._harvest_job_id,
            host=self._host,
            path=path,
            method=method,
            status=status,
            duration_ms=duration_ms,
            auth_type_used="ssh_key" if "private_key_pem" in self._secret.fields else "ssh_password",
            error=error,
        ))

    def connect(self) -> None:
        # Re-validated here (not just at registration time) so a DNS
        # rebind between registration and this scheduled run is caught --
        # same requirement as the HTTP transport.
        assert_safe_network_target(
            self._host, self._port, allow_private_ranges=self._allow_private_ranges
        )

        started = time.monotonic()
        error = None
        try:
            self._transport = self._transport_factory((self._host, self._port))
            self._transport.start_client(timeout=self._connect_timeout_s)

            key = self._transport.get_remote_server_key()
            fingerprint = _fingerprint(key) if key else None
            if self._pinned_host_key_fingerprint is None:
                raise HostKeyMismatchError(
                    "No host key has been pinned for this source -- register it via "
                    "provider_source_fetch_host_key first"
                )
            if fingerprint != self._pinned_host_key_fingerprint:
                raise HostKeyMismatchError(
                    "Host key fingerprint changed: expected %s, got %s"
                    % (self._pinned_host_key_fingerprint, fingerprint)
                )

            self._authenticate()
            self._sftp = self._sftp_client_factory(self._transport)
        except Exception as exc:  # noqa: BLE001 -- reported then re-raised, not swallowed
            error = str(exc)
            raise
        finally:
            self._report(
                "CONNECT", self._remote_path,
                status=None, duration_ms=(time.monotonic() - started) * 1000, error=error,
            )

    def _authenticate(self) -> None:
        fields = self._secret.fields
        username = fields.get("username")
        if not username:
            raise ValueError("SSH secret bundle is missing 'username'")

        if "private_key_pem" in fields:
            import io
            pkey = paramiko.RSAKey.from_private_key(
                io.StringIO(fields["private_key_pem"]),
                password=fields.get("private_key_passphrase") or None,
            )
            self._transport.auth_publickey(username, pkey)
        elif "password" in fields:
            self._transport.auth_password(username, fields["password"])
        else:
            raise ValueError(
                "SSH secret bundle needs either 'password' or 'private_key_pem'"
            )

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def list_entries(self, cursor: Optional[str]) -> Page:
        # One page, always: a remote directory listing is bounded (unlike
        # an HTTP API that could paginate over an unbounded record set),
        # so there's no cursor state to carry between calls.
        if cursor is not None:
            return Page(entries=[], next_cursor=None)

        assert self._sftp is not None, "connect() must be called before listing"
        started = time.monotonic()
        error = None
        try:
            attrs = self._sftp.listdir_attr(self._remote_path)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            raise
        finally:
            self._report(
                "LIST", self._remote_path,
                status=None, duration_ms=(time.monotonic() - started) * 1000, error=error,
            )

        entries = [
            Entry(
                ref=self._remote_path.rstrip("/") + "/" + a.filename,
                metadata={"mtime": a.st_mtime, "size": a.st_size},
            )
            for a in attrs
            if not stat.S_ISDIR(a.st_mode or 0)
            and fnmatch.fnmatch(a.filename, self._glob_pattern)
        ]
        return Page(entries=entries, next_cursor=None)

    def open_entry(self, entry: Entry) -> BinaryIO:
        assert self._sftp is not None, "connect() must be called before opening a file"
        started = time.monotonic()
        error = None
        try:
            handle = self._sftp.open(entry.ref, "rb")
            # Buffered, chunked reads instead of paramiko's default
            # request-per-read-call behaviour -- meaningfully faster for
            # large files without ever holding a whole file in memory.
            handle.prefetch()
            return handle
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            raise
        finally:
            self._report(
                "GET", entry.ref,
                status=None, duration_ms=(time.monotonic() - started) * 1000, error=error,
            )
