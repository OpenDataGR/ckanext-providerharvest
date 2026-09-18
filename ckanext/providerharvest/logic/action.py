"""Custom actions for provider self-service source management.

These wrap ckanext-harvest's own actions (so org-scoping and validation
stay centralized in CKAN/ckanext-harvest) rather than writing directly to
HarvestSource, and layer on the provider-specific pieces: encrypted
secrets, the ProviderSourceExtension row, field mapping, and the
CKAN package/resource + DataStore setup a source needs before its first
real harvest run.
"""

from __future__ import annotations

import datetime
import json
import uuid

from ckan.plugins import toolkit

from ckanext.providerharvest.mapping import FieldMappingProfile
from ckanext.providerharvest.model import field_mapping as field_mapping_model
from ckanext.providerharvest.model import provider_source as provider_source_model
from ckanext.providerharvest.secrets.envelope import EnvelopeSecretsBackend
from ckanext.providerharvest.model.secret import SqlAlchemySecretRepository
from ckanext.providerharvest.logic.schema import (
    NESTED_FIELDS,
    REQUIRED_NESTED_FIELDS,
    provider_source_create_schema,
)


def _secrets_backend():
    return EnvelopeSecretsBackend(repository=SqlAlchemySecretRepository())


def _pop_nested_fields(data_dict: dict) -> dict:
    """Pull out the list/dict-valued fields navl_validate can't handle as
    opaque values (see schema.py's module docstring), validate the
    required ones are non-empty by hand, and return them separately from
    what's left to run through navl_validate.

    ``row_rules`` (the field-mapping rows) is only meaningful for
    ``delivery_mode="api_records"`` -- a bulk-file source has no
    per-record fields to map, it just streams whole files -- so it drops
    out of the required set for ``delivery_mode="bulk_file"``.
    """
    nested = {k: data_dict.pop(k, None) for k in NESTED_FIELDS}
    required = set(REQUIRED_NESTED_FIELDS)
    if data_dict.get("delivery_mode") == "bulk_file":
        required.discard("row_rules")
    errors = {
        field: ["Missing value"]
        for field in required
        if not nested.get(field)
    }
    if errors:
        raise toolkit.ValidationError(errors)
    return {k: v for k, v in nested.items() if v is not None}


def _parse_host(endpoint_url: str) -> str:
    """SFTP/SCP/FTP sources reuse ``endpoint_url`` for the host (no
    separate "host" field) -- accepts either a bare hostname or a
    URL-ish ``sftp://host[:port]``/``ftp://host[:port]`` string,
    matching what a provider would plausibly type into the same field
    the HTTP flow uses for its URL."""
    from urllib.parse import urlparse
    if "://" in endpoint_url:
        return urlparse(endpoint_url).hostname or endpoint_url
    return endpoint_url.split("/")[0].split(":")[0]


#: Transports that connect over SSH -- see base_generic.SSH_TRANSPORT_TYPES
#: for the harvester-side counterpart of this same grouping.
SSH_TRANSPORT_TYPES = ("sftp", "scp")


def _require_ssh_fields(data_dict: dict) -> None:
    if data_dict.get("transport_type") not in SSH_TRANSPORT_TYPES:
        return
    errors = {}
    if not data_dict.get("host_key_fingerprint"):
        errors["host_key_fingerprint"] = [
            "Required for transport_type=%s -- fetch and confirm it via "
            "provider_source_fetch_host_key first" % data_dict.get("transport_type")
        ]
    if errors:
        raise toolkit.ValidationError(errors)


def _parse_bool(value, default: bool) -> bool:
    """Form fields arrive as strings ("true"/"on"/...), direct Action API
    calls may pass real booleans -- accept either, same tolerance the
    checkbox-style row_rules fields already need in the blueprint."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "on", "yes")


def _require_ftp_fields(data_dict: dict) -> bool:
    """Returns the resolved use_tls value; raises if plain (unencrypted)
    FTP was requested without the explicit acknowledgment DESIGN.md's
    Phase 1.5 notes require -- see transport/ftp.py's own docstring for
    why this can't be silently defaulted."""
    if data_dict.get("transport_type") != "ftp":
        return True
    use_tls = _parse_bool(data_dict.get("use_tls"), default=True)
    if not use_tls and not _parse_bool(data_dict.get("plain_ftp_acknowledged"), default=False):
        raise toolkit.ValidationError({
            "plain_ftp_acknowledged": [
                "Required when use_tls is disabled -- plain FTP sends credentials and "
                "file contents unencrypted. Acknowledge this explicitly, or leave "
                "use_tls enabled (the default) to use FTPS instead."
            ]
        })
    return use_tls


@toolkit.side_effect_free
def provider_source_list_mine(context, data_dict):
    toolkit.check_access("provider_source_list_mine", context, data_dict)
    user = context["user"]
    orgs = toolkit.get_action("organization_list_for_user")(
        dict(context), {"id": user, "permission": "update_dataset"}
    )
    org_ids = {org["id"] for org in orgs}
    if not org_ids:
        return []

    # Not harvest_source_list/package_search: ckanext-harvest deliberately
    # keeps harvest-source packages out of the Solr search index (they
    # back a source's *configuration*, not a real dataset) -- confirmed
    # by direct testing: a `package_search` for the harvest-type package
    # comes back empty even with include_private + ignore_auth, while the
    # underlying HarvestSource DB row is unquestionably there and active.
    # Query the package table directly instead of anything search-backed.
    from ckan.model import Package, Session
    packages = (
        Session.query(Package)
        .filter(Package.type == "harvest")
        .filter(Package.state == "active")
        .filter(Package.owner_org.in_(org_ids))
        .all()
    )
    return [
        toolkit.get_action("harvest_source_show")(
            {**context, "ignore_auth": True}, {"id": pkg.id}
        )
        for pkg in packages
    ]


def provider_source_test_connection(context, data_dict):
    """Bounded dry run: validates connectivity + previews the mapping,
    WITHOUT creating any HarvestObject/package/DataStore rows."""
    toolkit.check_access("provider_source_test_connection", context, data_dict)

    from ckanext.providerharvest.secrets.base import SecretBundle

    transport_type = data_dict.get("transport_type")
    if transport_type in SSH_TRANSPORT_TYPES:
        return _test_ssh_connection(data_dict, transport_type)
    if transport_type == "ftp":
        return _test_ftp_connection(data_dict)
    if transport_type != "http":
        raise toolkit.ValidationError(
            "transport_type %r is not yet implemented (available: http, %s, ftp)"
            % (transport_type, ", ".join(SSH_TRANSPORT_TYPES))
        )

    from ckanext.providerharvest.auth_strategies.api_key import ApiKeyAuth
    from ckanext.providerharvest.transport.direct_https import DirectHTTPSTransport

    secret = SecretBundle(fields=data_dict["credential_fields"])  # not yet persisted
    transport = DirectHTTPSTransport(
        base_url=data_dict["endpoint_url"],
        auth_strategy=ApiKeyAuth(),
        secret=secret,
        pagination=data_dict.get("pagination", {}),
        auth_opts=data_dict.get("auth_opts"),
        max_requests_per_minute=data_dict.get("max_requests_per_minute", 60),
    )
    with transport:
        page = transport.list_entries(cursor=None)

    sample_records = [
        json.loads(entry.inline_data.decode("utf-8"))
        for entry in page.entries[:5]
        if entry.inline_data
    ]

    preview_rows = None
    if data_dict.get("row_rules") and sample_records:
        profile = FieldMappingProfile(row_rules=[
            _rule_from_dict(r) for r in data_dict["row_rules"]
        ])
        preview_rows = [profile.extract_row(r) for r in sample_records]

    return {"sample_records": sample_records, "preview_rows": preview_rows}


def _test_ssh_connection(data_dict, transport_type):
    """SFTP/SCP's dry run lists the remote directory (path/mtime/size
    only, never file contents -- there's no per-record mapping to
    preview for a bulk-file source) and requires a fingerprint to
    already be pinned, so this exercises the exact same host-key check a
    real harvest run will make, not a weaker one."""
    from ckanext.providerharvest.secrets.base import SecretBundle
    from ckanext.providerharvest.transport.scp import ScpTransport
    from ckanext.providerharvest.transport.sftp import SFTPTransport

    _require_ssh_fields(data_dict)
    secret = SecretBundle(fields=data_dict["credential_fields"])  # not yet persisted
    transport_cls = SFTPTransport if transport_type == "sftp" else ScpTransport
    transport = transport_cls(
        _parse_host(data_dict["endpoint_url"]),
        secret=secret,
        port=int(data_dict.get("port") or 22),
        remote_path=data_dict.get("remote_path") or "/",
        glob_pattern=data_dict.get("glob_pattern") or "*",
        pinned_host_key_fingerprint=data_dict["host_key_fingerprint"],
    )
    with transport:
        page = transport.list_entries(cursor=None)

    sample_files = [
        {"ref": entry.ref, "mtime": entry.metadata.get("mtime"), "size": entry.metadata.get("size")}
        for entry in page.entries[:20]
    ]
    return {"sample_files": sample_files}


def _test_ftp_connection(data_dict):
    """Same shape as _test_ssh_connection, but FTP has no host-key
    pinning step -- FTPS uses ordinary TLS certificate verification
    instead (see transport/ftp.py), so there's nothing to pre-confirm
    before this dry run beyond the plain-FTP acknowledgment check
    _require_ftp_fields already enforces."""
    from ckanext.providerharvest.secrets.base import SecretBundle
    from ckanext.providerharvest.transport.ftp import FTPTransport

    use_tls = _require_ftp_fields(data_dict)
    secret = SecretBundle(fields=data_dict["credential_fields"])  # not yet persisted
    transport = FTPTransport(
        _parse_host(data_dict["endpoint_url"]),
        secret=secret,
        port=int(data_dict.get("port") or 21),
        remote_path=data_dict.get("remote_path") or "/",
        glob_pattern=data_dict.get("glob_pattern") or "*",
        use_tls=use_tls,
        plain_ftp_acknowledged=_parse_bool(data_dict.get("plain_ftp_acknowledged"), default=False),
    )
    with transport:
        page = transport.list_entries(cursor=None)

    sample_files = [
        {"ref": entry.ref, "mtime": entry.metadata.get("modify"), "size": entry.metadata.get("size")}
        for entry in page.entries[:20]
    ]
    return {"sample_files": sample_files}


def provider_source_fetch_host_key(context, data_dict):
    """Connects just far enough to read the SSH server's host key and
    returns its fingerprint for the provider to confirm, WITHOUT
    authenticating or persisting anything -- the trust-on-first-use step
    that must happen before a fingerprint can be pinned via
    ``provider_source_create``'s ``host_key_fingerprint`` field. Same
    connection code (and same fingerprint) regardless of whether the
    source ends up using transport_type=sftp or scp -- both are the same
    SSH server, just a different subsystem."""
    toolkit.check_access("provider_source_fetch_host_key", context, data_dict)

    from ckanext.providerharvest.transport.ssh_common import fetch_host_key_fingerprint

    host = _parse_host(data_dict["endpoint_url"])
    fingerprint = fetch_host_key_fingerprint(host, int(data_dict.get("port") or 22))
    return {"host": host, "fingerprint": fingerprint}


def _rule_from_dict(d):
    from ckanext.providerharvest.mapping import FieldMappingRule
    return FieldMappingRule(
        ckan_field=d["ckan_field"],
        source_path=d["source_path"],
        field_type=d.get("field_type", "text"),
        transform=d.get("transform"),
        is_primary_key=d.get("is_primary_key", False),
        required=d.get("required", False),
    )


def provider_source_create(context, data_dict):
    """Registers a new provider source: encrypts the credential, creates
    the underlying ckanext-harvest HarvestSource (org-scoped, so CKAN
    itself enforces ownership), and stores our extension row in
    'pending' status -- it will not run until an admin approves it
    (see provider_source_activate)."""
    toolkit.check_access("provider_source_create", context, data_dict)
    nested = _pop_nested_fields(data_dict)
    data_dict, errors = toolkit.navl_validate(
        data_dict, provider_source_create_schema(), context
    )
    if errors:
        raise toolkit.ValidationError(errors)
    data_dict.update(nested)
    _require_ssh_fields(data_dict)
    ftp_use_tls = _require_ftp_fields(data_dict)

    owner_org = data_dict["owner_org"]
    # The secret's harvest_source_id column is bookkeeping/audit metadata
    # only -- secret_ref alone is what's needed to resolve it later, so a
    # placeholder here (we don't have the real HarvestSource id until
    # after harvest_source_create below) doesn't affect correctness.
    secret_ref = _secrets_backend().put(
        harvest_source_id="pending-" + str(uuid.uuid4()),
        fields=data_dict["credential_fields"],
    )

    config = {
        "transport_type": data_dict["transport_type"],
        "auth_type": data_dict["auth_type"],
        "secret_ref": secret_ref,
        "pagination": data_dict.get("pagination", {}),
        "auth_opts": data_dict.get("auth_opts"),
        "delivery_mode": data_dict.get("delivery_mode", "api_records"),
    }
    if data_dict["transport_type"] in SSH_TRANSPORT_TYPES:
        config.update({
            "host": _parse_host(data_dict["endpoint_url"]),
            "port": int(data_dict.get("port") or 22),
            "remote_path": data_dict.get("remote_path") or "/",
            "glob_pattern": data_dict.get("glob_pattern") or "*",
        })
    elif data_dict["transport_type"] == "ftp":
        config.update({
            "host": _parse_host(data_dict["endpoint_url"]),
            "port": int(data_dict.get("port") or 21),
            "remote_path": data_dict.get("remote_path") or "/",
            "glob_pattern": data_dict.get("glob_pattern") or "*",
            "use_tls": ftp_use_tls,
        })

    harvest_source = toolkit.get_action("harvest_source_create")(dict(context), {
        "url": data_dict["endpoint_url"],
        "name": data_dict["name"],
        "title": data_dict.get("title", data_dict["name"]),
        "source_type": "provider_api",
        "owner_org": owner_org,
        "frequency": data_dict.get("frequency", "DAILY"),
        "config": json.dumps(config),
    })

    profile = FieldMappingProfile(
        row_rules=[_rule_from_dict(r) for r in data_dict.get("row_rules", [])],
        dataset_defaults=data_dict.get("dataset_defaults", {}),
        resource_defaults=data_dict.get("resource_defaults", {}),
    )
    field_mapping_model.save(harvest_source["id"], profile)

    provider_source = provider_source_model.create(
        harvest_source_id=harvest_source["id"],
        owner_org=owner_org,
        delivery_mode=config["delivery_mode"],
        status="pending",
        notification_email=data_dict.get("notification_email"),
        notification_webhook_url=data_dict.get("notification_webhook_url"),
        host_key_fingerprint=data_dict.get("host_key_fingerprint"),
        plain_ftp_acknowledged=(
            not ftp_use_tls if data_dict["transport_type"] == "ftp" else False
        ),
        created=datetime.datetime.utcnow(),
    )

    return {
        "harvest_source_id": harvest_source["id"],
        "status": provider_source.status,
    }


@toolkit.side_effect_free
def provider_source_list_pending(context, data_dict):
    """Sysadmin approval queue: every source awaiting activation, across
    all orgs -- deliberately not org-scoped, unlike
    provider_source_list_mine, since approving sources is a data.gov.gr
    admin responsibility, not something an org does for itself."""
    toolkit.check_access("provider_source_list_pending", context, data_dict)
    pending = provider_source_model.list_by_status("pending")
    results = []
    for provider_source in pending:
        try:
            harvest_source = toolkit.get_action("harvest_source_show")(
                {**context, "ignore_auth": True}, {"id": provider_source.harvest_source_id}
            )
        except toolkit.ObjectNotFound:
            continue
        results.append({
            "harvest_source_id": provider_source.harvest_source_id,
            "name": harvest_source["name"],
            "title": harvest_source.get("title") or harvest_source["name"],
            "url": harvest_source["url"],
            "owner_org": provider_source.owner_org,
            "organization_title": (harvest_source.get("organization") or {}).get("title"),
            "created": provider_source.created,
            "notification_email": provider_source.notification_email,
        })
    return results


def provider_source_activate(context, data_dict):
    """data.gov.gr admin action: approves a pending source, provisioning
    its CKAN package + DataStore-backed resource, then flips it to
    'active' so scheduled runs will actually execute."""
    toolkit.check_access("provider_source_activate", context, data_dict)

    harvest_source_id = data_dict["harvest_source_id"]
    provider_source = provider_source_model.get_by_harvest_source_id(harvest_source_id)
    if provider_source is None:
        raise toolkit.ObjectNotFound("No provider source for %r" % harvest_source_id)

    harvest_source = toolkit.get_action("harvest_source_show")(
        dict(context), {"id": harvest_source_id}
    )
    profile = field_mapping_model.load_latest(harvest_source_id)

    package_id, resource_id = ensure_provider_resource(
        context, harvest_source, profile, delivery_mode=provider_source.delivery_mode
    )
    provider_source.ckan_package_id = package_id
    provider_source.ckan_resource_id = resource_id
    provider_source.status = "active"

    from ckan.model.meta import Session
    Session.commit()

    return {"harvest_source_id": harvest_source_id, "status": "active"}


def ensure_provider_resource(context, harvest_source: dict, profile: FieldMappingProfile,
                              *, delivery_mode: str = "api_records"):
    """Idempotently creates the CKAN package this provider source's data
    lands in, using data.gov.gr's real 'dataset' scheming schema fields
    (confirmed via scheming_dataset_schema_show).

    For ``delivery_mode="api_records"``, also creates the single
    DataStore-backed resource every record load writes into, plus a
    matching 'data-service' entry so the DataStore query API is a
    discoverable catalog entry. For ``delivery_mode="bulk_file"`` there
    is no such single resource to pre-create -- each remote file becomes
    its own resource the first time ``import_stage`` streams it (see
    ``FileResourceLoader``) -- so only the package is created and
    ``resource_id`` comes back ``None``.
    """
    package_dict = dict(profile.dataset_defaults)
    # NOT harvest_source["name"]: ckanext-harvest's own HarvestSource is
    # itself backed by a CKAN package with type="harvest" under that
    # exact name (see the "Creating harvest source" log line at
    # harvest_source_create time) -- reusing it here collides with that
    # package 100% of the time, not just occasionally. Matches the
    # "-api" suffix already used below for the data-service package.
    package_dict.setdefault("name", "%s-data" % harvest_source["name"])
    package_dict["owner_org"] = harvest_source["organization"]["id"] \
        if isinstance(harvest_source.get("organization"), dict) else harvest_source["owner_org"]
    package_dict["type"] = "dataset"

    package = toolkit.get_action("package_create")(dict(context), package_dict)

    if delivery_mode == "bulk_file":
        return package["id"], None

    resource_dict = dict(profile.resource_defaults)
    resource_dict.update({
        "package_id": package["id"],
        "name": resource_dict.get("name") or harvest_source["name"],
        "url": harvest_source["url"],
        "format": resource_dict.get("format", "CSV"),
    })
    resource = toolkit.get_action("resource_create")(dict(context), resource_dict)

    site_url = toolkit.config.get("ckan.site_url", "").rstrip("/")
    datastore_endpoint = "%s/api/3/action/datastore_search?resource_id=%s" % (
        site_url, resource["id"]
    )
    data_service = toolkit.get_action("package_create")(dict(context), {
        "type": "data-service",
        "name": "%s-api" % package["name"],
        "title": "%s -- Query API" % package_dict.get("title", package["name"]),
        "endpoint_url": datastore_endpoint,
        "endpoint_description": "CKAN DataStore search API for this dataset's data.",
        "owner_org": package_dict["owner_org"],
    })
    toolkit.get_action("resource_patch")(dict(context), {
        "id": resource["id"],
        "access_services": [data_service["id"]],
    })

    return package["id"], resource["id"]
