"""The one harvester class for every provider source.

Behaviour is entirely driven by ``HarvestSource.config`` (transport_type,
auth_type, secret_ref, pagination, delivery_mode) plus the
``ProviderSourceExtension``/``FieldMappingProfile`` side-tables -- there is
no per-provider subclass. Phase 1 implements exactly one transport
("http") and one auth strategy ("api_key"); other values raise a clear
"not yet implemented" error rather than failing silently, so adding
SFTP/SCP/FTP later is additive.
"""

from __future__ import annotations

import datetime
import io
import json
import logging

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
from ckanext.providerharvest.model import provider_source as provider_source_model
from ckanext.providerharvest.parsers.json_records import JSONRecordsParser
from ckanext.providerharvest.secrets.base import SecretsBackend
from ckanext.providerharvest.secrets.envelope import EnvelopeSecretsBackend
from ckanext.providerharvest.model.secret import SqlAlchemySecretRepository
from ckanext.providerharvest.transport.base import Transport
from ckanext.providerharvest.transport.direct_https import DirectHTTPSTransport

log = logging.getLogger(__name__)

_AUTH_STRATEGIES: dict[str, type[AuthStrategy]] = {
    "api_key": ApiKeyAuth,
}


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

        required = ["transport_type", "auth_type", "secret_ref", "pagination"]
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

        if data["transport_type"] != "http":
            raise toolkit.ValidationError(
                "transport_type %r is not yet implemented (Phase 1 supports only 'http')"
                % data["transport_type"]
            )
        if data.get("delivery_mode", "api_records") != "api_records":
            raise toolkit.ValidationError(
                "delivery_mode %r is not yet implemented (Phase 1 supports only 'api_records')"
                % data["delivery_mode"]
            )

        return json.dumps(data)

    # -- internal helpers -------------------------------------------------

    def _build_transport(self, source, job, config: dict) -> Transport:
        secret = self._secrets_backend.get(config["secret_ref"])
        auth_strategy = _build_auth_strategy(config["auth_type"])

        def on_request(event: OutboundRequestEvent) -> None:
            audit_log_model.record_event(event)

        if config["transport_type"] == "http":
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

        object_ids = []
        try:
            with transport:
                cursor = None
                while True:
                    page = transport.list_entries(cursor)
                    for entry in page.entries:
                        obj = HarvestObject(
                            guid=entry.ref,
                            job=harvest_job,
                            content=entry.inline_data.decode("utf-8") if entry.inline_data else None,
                        )
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

        context = {"model": None, "session": None, "ignore_auth": True, "user": "harvest"}
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
