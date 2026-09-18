"""The one harvester class for every provider source.

Behaviour is entirely driven by ``HarvestSource.config`` (transport_type,
auth_type, secret_ref, pagination, delivery_mode) plus the
``ProviderSourceExtension``/``FieldMappingProfile`` side-tables -- there is
no per-provider subclass. Supported transports: "http" (api_records),
"sftp"/"scp"/"ftp" (bulk_file). Unsupported transport_type/delivery_mode
combinations raise a clear "not yet implemented" error rather than
failing silently.
"""

from __future__ import annotations

import datetime
import io
import json
import logging

from ckan import model as ckan_model
from ckan.plugins import toolkit
from ckanext.harvest.harvesters.base import HarvesterBase
from ckanext.harvest.model import HarvestObject

from ckanext.providerharvest import notifications
from ckanext.providerharvest.audit import OutboundRequestEvent
from ckanext.providerharvest.auth_strategies.api_key import ApiKeyAuth
from ckanext.providerharvest.auth_strategies.base import AuthStrategy
from ckanext.providerharvest.logic.validators import UnsafeNetworkTargetError
from ckanext.providerharvest.loaders.datastore_loader import DataStoreLoader
from ckanext.providerharvest.mapping import FieldMappingError
from ckanext.providerharvest.model import audit_log as audit_log_model
from ckanext.providerharvest.model import field_mapping as field_mapping_model
from ckanext.providerharvest.loaders.file_resource_loader import FileResourceLoader
from ckanext.providerharvest.model import provider_source as provider_source_model
from ckanext.providerharvest.parsers.json_records import JSONRecordsParser
from ckanext.providerharvest.secrets.base import SecretsBackend
from ckanext.providerharvest.secrets.envelope import EnvelopeSecretsBackend
from ckanext.providerharvest.model.secret import SqlAlchemySecretRepository
from ckanext.providerharvest.transport.base import Entry, Transport
from ckanext.providerharvest.transport.direct_https import DirectHTTPSTransport
from ckanext.providerharvest.transport.ftp import FTPTransport
from ckanext.providerharvest.transport.scp import ScpTransport
from ckanext.providerharvest.transport.sftp import SFTPTransport

log = logging.getLogger(__name__)

_AUTH_STRATEGIES: dict[str, type[AuthStrategy]] = {
    "api_key": ApiKeyAuth,
}

#: Transports that connect over SSH (paramiko), sharing the exact same
#: connection/host-key-pinning/auth code via transport.ssh_common -- only
#: the file-listing/reading mechanics differ between them. See
#: DESIGN.md's Phase 1.5 notes.
SSH_TRANSPORT_TYPES = ("sftp", "scp")

#: Every whole-file transport (delivery_mode=bulk_file): the SSH ones
#: plus FTP/FTPS, which needs the same host/remote_path config shape but
#: isn't SSH-based (no host-key pinning -- FTPS uses ordinary TLS
#: certificate verification instead, see transport/ftp.py).
BULK_FILE_TRANSPORT_TYPES = SSH_TRANSPORT_TYPES + ("ftp",)


def default_secrets_backend() -> SecretsBackend:
    return EnvelopeSecretsBackend(repository=SqlAlchemySecretRepository())


def _build_auth_strategy(auth_type: str) -> AuthStrategy:
    strategy_cls = _AUTH_STRATEGIES.get(auth_type)
    if strategy_cls is None:
        raise toolkit.ValidationError(
            "auth_type %r is not yet implemented (available: %s)"
            % (auth_type, sorted(_AUTH_STRATEGIES))
        )
    return strategy_cls()


class GenericProviderHarvester(HarvesterBase):
    """See module docstring. Registered under ``IHarvester`` in plugin.py."""

    def __init__(self, secrets_backend: SecretsBackend | None = None):
        self._secrets_backend = secrets_backend or default_secrets_backend()

    def info(self):
        return {
            "name": "provider_api",
            "title": "Provider API Harvester",
            "description": (
                "Generic, self-service-configurable harvester for provider "
                "REST APIs (and, in a later phase, SFTP/SCP/FTP sources)."
            ),
        }

    def validate_config(self, config: str | None) -> str:
        if not config:
            raise toolkit.ValidationError("Config is required")
        try:
            data = json.loads(config)
        except ValueError as exc:
            raise toolkit.ValidationError("Config must be valid JSON: %s" % exc) from exc

        required = ["transport_type", "auth_type", "secret_ref"]
        transport_type = data.get("transport_type")
        if transport_type == "http":
            required.append("pagination")
        elif transport_type in BULK_FILE_TRANSPORT_TYPES:
            # NOT host_key_fingerprint: that's per-source trust state,
            # stored on ProviderSourceExtension.host_key_fingerprint (see
            # provider_source_create) and read from there by
            # _build_transport -- it deliberately never goes into this
            # config blob at all, so requiring it here would reject
            # every real SFTP/SCP source at creation time (confirmed:
            # this broke the SFTP integration test's very first
            # harvest_source_create call). Not applicable to ftp anyway,
            # which has no host-key concept at all.
            required += ["host", "remote_path"]
        missing = [key for key in required if key not in data]
        if missing:
            raise toolkit.ValidationError("Config missing required keys: %s" % missing)

        # Secrets must never appear directly in config -- only a secret_ref.
        forbidden = {"api_key", "password", "client_secret", "private_key", "private_key_pem"}
        leaked = forbidden & set(data)
        if leaked:
            raise toolkit.ValidationError(
                "Config must not contain raw credentials (found: %s) -- "
                "store them via the secrets backend and reference by secret_ref" % sorted(leaked)
            )

        supported_transports = ("http",) + BULK_FILE_TRANSPORT_TYPES
        if transport_type not in supported_transports:
            raise toolkit.ValidationError(
                "transport_type %r is not yet implemented (available: %s)"
                % (transport_type, ", ".join(supported_transports))
            )

        delivery_mode = data.get("delivery_mode", "api_records")
        # Each transport currently only supports the one delivery_mode
        # that actually makes sense for it: an HTTP JSON API yields
        # per-record data to map into DataStore, an SFTP/SCP/FTP
        # directory yields whole files to store as resources -- see
        # DESIGN.md's Phase 1.5 notes on why these aren't cross-combined
        # (yet).
        valid_combinations = {
            "http": "api_records", "sftp": "bulk_file", "scp": "bulk_file", "ftp": "bulk_file",
        }
        if delivery_mode != valid_combinations[transport_type]:
            raise toolkit.ValidationError(
                "delivery_mode %r is not supported for transport_type=%r "
                "(expected %r)" % (delivery_mode, transport_type, valid_combinations[transport_type])
            )
        data["delivery_mode"] = delivery_mode

        return json.dumps(data)

    # -- internal helpers -------------------------------------------------

    def _build_transport(self, source, job, config: dict) -> Transport:
        secret = self._secrets_backend.get(config["secret_ref"])

        def on_request(event: OutboundRequestEvent) -> None:
            audit_log_model.record_event(event)

        if config["transport_type"] == "http":
            # auth_type/AuthStrategy is an HTTP-specific concept (how to
            # attach credentials to a request) -- SSH auth (password vs.
            # private key) is decided from the secret's own shape inside
            # ssh_common.connect_and_authenticate instead, so this is
            # deliberately not called for the SSH transports below.
            auth_strategy = _build_auth_strategy(config["auth_type"])
            return DirectHTTPSTransport(
                base_url=source.url,
                auth_strategy=auth_strategy,
                secret=secret,
                pagination=config["pagination"],
                auth_opts=config.get("auth_opts"),
                ca_bundle_path=config.get("ca_bundle_path"),
                max_requests_per_minute=config.get("max_requests_per_minute", 60),
                allow_private_ranges=config.get("allow_private_ranges", False),
                on_request=on_request,
                harvest_source_id=source.id,
                harvest_job_id=job.id,
            )
        if config["transport_type"] in SSH_TRANSPORT_TYPES:
            provider_source = provider_source_model.get_by_harvest_source_id(source.id)
            transport_cls = SFTPTransport if config["transport_type"] == "sftp" else ScpTransport
            return transport_cls(
                config["host"],
                secret=secret,
                port=config.get("port", 22),
                remote_path=config["remote_path"],
                glob_pattern=config.get("glob_pattern", "*"),
                # Pinned at registration time via provider_source_create's
                # host_key_fingerprint field (see
                # provider_source_fetch_host_key) -- not config, since
                # it's per-source trust state, not transport behaviour.
                # Shared between sftp/scp: same SSH server, same key.
                pinned_host_key_fingerprint=(
                    provider_source.host_key_fingerprint if provider_source else None
                ),
                allow_private_ranges=config.get("allow_private_ranges", False),
                on_request=on_request,
                harvest_source_id=source.id,
                harvest_job_id=job.id,
            )
        if config["transport_type"] == "ftp":
            provider_source = provider_source_model.get_by_harvest_source_id(source.id)
            return FTPTransport(
                config["host"],
                secret=secret,
                port=config.get("port", 21),
                remote_path=config["remote_path"],
                glob_pattern=config.get("glob_pattern", "*"),
                use_tls=config.get("use_tls", True),
                # Per-source risk acceptance, stored on
                # ProviderSourceExtension (like host_key_fingerprint) --
                # not config, so it can't be silently defaulted to True
                # by editing HarvestSource.config directly.
                plain_ftp_acknowledged=(
                    bool(provider_source.plain_ftp_acknowledged) if provider_source else False
                ),
                allow_private_ranges=config.get("allow_private_ranges", False),
                on_request=on_request,
                harvest_source_id=source.id,
                harvest_job_id=job.id,
            )
        raise toolkit.ValidationError("Unsupported transport_type %r" % config["transport_type"])

    def _record_failure_and_maybe_notify(self, source, reason: str, detail: str = "") -> None:
        provider_source = provider_source_model.record_failure(source.id)
        if notifications.should_notify(provider_source.consecutive_failure_count):
            notifications.notify(
                provider_source,
                source_title=source.title or source.name,
                failed_at=datetime.datetime.utcnow().isoformat(),
                reason=reason,
                detail=detail,
            )

    # -- HarvesterBase interface -------------------------------------------

    def gather_stage(self, harvest_job):
        source = harvest_job.source
        provider_source = provider_source_model.get_by_harvest_source_id(source.id)
        if provider_source is None or provider_source.status != "active":
            log.info("Source %s is not active (status=%s) -- skipping run",
                      source.id, provider_source.status if provider_source else "unregistered")
            return []

        config = json.loads(source.config)

        try:
            transport = self._build_transport(source, harvest_job, config)
        except UnsafeNetworkTargetError as exc:
            self._save_gather_error("Network target rejected: %s" % exc, harvest_job)
            self._record_failure_and_maybe_notify(source, "network_target_rejected", str(exc))
            return []

        bulk_file = config.get("delivery_mode") == "bulk_file"
        object_ids = []
        try:
            with transport:
                cursor = None
                while True:
                    page = transport.list_entries(cursor)
                    for entry in page.entries:
                        if bulk_file:
                            # Never the file bytes here -- provider
                            # exports can be multi-GB, and gather_stage
                            # may run in a different process/lifetime
                            # than import_stage. Only the pointer
                            # (remote ref + mtime/size) is persisted;
                            # import_stage re-opens the file itself.
                            content = json.dumps({"ref": entry.ref, "metadata": entry.metadata})
                        else:
                            content = entry.inline_data.decode("utf-8") if entry.inline_data else None
                        obj = HarvestObject(guid=entry.ref, job=harvest_job, content=content)
                        obj.save()
                        object_ids.append(obj.id)
                    if not page.next_cursor:
                        break
                    cursor = page.next_cursor
        except Exception as exc:  # noqa: BLE001 -- surfaced via ckanext-harvest's own error reporting
            self._save_gather_error("Error fetching from provider: %s" % exc, harvest_job)
            reason = "timeout" if "timeout" in str(exc).lower() else "unknown"
            self._record_failure_and_maybe_notify(source, reason, str(exc))
            return []

        return object_ids

    def fetch_stage(self, harvest_object):
        # The direct-HTTPS transport already fetches full record bodies
        # during gather_stage (see DirectHTTPSTransport.list_entries), so
        # there is nothing left to do here for Phase 1.
        return True

    def import_stage(self, harvest_object):
        source = harvest_object.job.source
        config = json.loads(source.config)
        if config.get("delivery_mode") == "bulk_file":
            return self._import_bulk_file(harvest_object, source, config)
        return self._import_api_record(harvest_object, source)

    def _import_api_record(self, harvest_object, source):
        try:
            record = JSONRecordsParser().parse(
                io.BytesIO(harvest_object.content.encode("utf-8"))
            )
            record = next(record)
        except Exception as exc:  # noqa: BLE001
            self._save_object_error("Could not parse record: %s" % exc, harvest_object)
            self._record_failure_and_maybe_notify(source, "mapping_error", str(exc))
            return False

        try:
            profile = field_mapping_model.load_latest(source.id)
            row = profile.extract_row(record)
        except (FieldMappingError, LookupError) as exc:
            self._save_object_error("Field mapping failed: %s" % exc, harvest_object)
            self._record_failure_and_maybe_notify(source, "mapping_error", str(exc))
            return False

        context = self._real_model_context()
        provider_source = provider_source_model.get_by_harvest_source_id(source.id)
        loader = DataStoreLoader(get_action=toolkit.get_action)

        try:
            resource_id = provider_source.ckan_resource_id
            if resource_id is None:
                # First object of this source: caller (logic.action) is
                # expected to have already created the package/resource
                # before activation -- see ensure_provider_resource.
                raise RuntimeError(
                    "Source %s has no ckan_resource_id -- it must be approved/"
                    "activated (which provisions the resource) before harvesting" % source.id
                )

            if not provider_source.datastore_initialized:
                loader.ensure_datastore_schema(context, resource_id, profile)
                provider_source.datastore_initialized = True

            loader.upsert_rows(context, resource_id, [row])
        except Exception as exc:  # noqa: BLE001
            self._save_object_error("DataStore load failed: %s" % exc, harvest_object)
            self._record_failure_and_maybe_notify(source, "unknown", str(exc))
            return False

        harvest_object.package_id = provider_source.ckan_package_id
        harvest_object.current = True
        harvest_object.save()
        provider_source_model.record_success(source.id)
        return True

    def _import_bulk_file(self, harvest_object, source, config):
        try:
            pointer = json.loads(harvest_object.content)
            ref, metadata = pointer["ref"], pointer["metadata"]
        except Exception as exc:  # noqa: BLE001
            self._save_object_error("Could not parse file pointer: %s" % exc, harvest_object)
            self._record_failure_and_maybe_notify(source, "mapping_error", str(exc))
            return False

        context = self._real_model_context()
        provider_source = provider_source_model.get_by_harvest_source_id(source.id)
        package_id = provider_source.ckan_package_id if provider_source else None
        if package_id is None:
            self._save_object_error(
                "Source %s has no ckan_package_id -- it must be approved/activated "
                "before harvesting" % source.id, harvest_object,
            )
            self._record_failure_and_maybe_notify(source, "unknown", "not activated")
            return False

        loader = FileResourceLoader(get_action=toolkit.get_action)
        try:
            # A fresh transport/connection per object: gather_stage's own
            # connection is long closed by the time import_stage runs
            # (a separate stage, possibly a separate process), and a
            # remote SFTP directory listing gives no way to keep a
            # single file handle alive across that boundary anyway.
            transport = self._build_transport(source, harvest_object.job, config)
            with transport:
                handle = transport.open_entry(Entry(ref=ref, metadata=metadata))
                try:
                    loader.load_stream(
                        context, package_id,
                        filename=ref.rsplit("/", 1)[-1], stream=handle,
                    )
                finally:
                    close = getattr(handle, "close", None)
                    if close:
                        close()
        except Exception as exc:  # noqa: BLE001
            self._save_object_error("File resource load failed: %s" % exc, harvest_object)
            self._record_failure_and_maybe_notify(source, "unknown", str(exc))
            return False

        harvest_object.package_id = package_id
        harvest_object.current = True
        harvest_object.save()
        provider_source_model.record_success(source.id)
        return True

    @staticmethod
    def _real_model_context() -> dict:
        # Not {"model": None, "session": None, ...}: ckanext-datastore's
        # own actions (datastore_create/datastore_upsert) dereference
        # context['model'] directly (e.g. for resource lookups), and
        # resource_create/update dereference it too -- passing None
        # instead of the real ckan.model module fails with "'NoneType'
        # object has no attribute 'query'" the moment a real write is
        # attempted, confirmed by actually running a harvest job.
        return {
            "model": ckan_model, "session": ckan_model.Session,
            "ignore_auth": True, "user": "harvest",
        }
