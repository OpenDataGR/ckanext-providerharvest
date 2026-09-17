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
    what's left to run through navl_validate."""
    nested = {k: data_dict.pop(k, None) for k in NESTED_FIELDS}
    errors = {
        field: ["Missing value"]
        for field in REQUIRED_NESTED_FIELDS
        if not nested.get(field)
    }
    if errors:
        raise toolkit.ValidationError(errors)
    return {k: v for k, v in nested.items() if v is not None}


@toolkit.side_effect_free
def provider_source_list_mine(context, data_dict):
    toolkit.check_access("provider_source_list_mine", context, data_dict)
    user = context["user"]
    orgs = toolkit.get_action("organization_list_for_user")(
        dict(context), {"id": user, "permission": "update_dataset"}
    )
    org_names = {org["name"] for org in orgs}

    all_sources = toolkit.get_action("harvest_source_list")(dict(context), {})
    return [s for s in all_sources if s.get("organization", {}).get("name") in org_names]


def provider_source_test_connection(context, data_dict):
    """Bounded dry run: validates connectivity + previews the mapping,
    WITHOUT creating any HarvestObject/package/DataStore rows."""
    toolkit.check_access("provider_source_test_connection", context, data_dict)

    from ckanext.providerharvest.auth_strategies.api_key import ApiKeyAuth
    from ckanext.providerharvest.secrets.base import SecretBundle
    from ckanext.providerharvest.transport.direct_https import DirectHTTPSTransport

    if data_dict.get("transport_type") != "http":
        raise toolkit.ValidationError("Only transport_type=http is implemented so far")

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
        row_rules=[_rule_from_dict(r) for r in data_dict["row_rules"]],
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
        created=datetime.datetime.utcnow(),
    )

    return {
        "harvest_source_id": harvest_source["id"],
        "status": provider_source.status,
    }


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

    package_id, resource_id = ensure_provider_resource(context, harvest_source, profile)
    provider_source.ckan_package_id = package_id
    provider_source.ckan_resource_id = resource_id
    provider_source.status = "active"

    from ckan.model.meta import Session
    Session.commit()

    return {"harvest_source_id": harvest_source_id, "status": "active"}


def ensure_provider_resource(context, harvest_source: dict, profile: FieldMappingProfile):
    """Idempotently creates the CKAN package/resource this provider
    source's data lands in, using data.gov.gr's real 'dataset' scheming
    schema fields (confirmed via scheming_dataset_schema_show), and
    registers a matching 'data-service' entry so the DataStore query API
    is a discoverable catalog entry, not just an un-cataloged endpoint.
    """
    package_dict = dict(profile.dataset_defaults)
    package_dict.setdefault("name", harvest_source["name"])
    package_dict["owner_org"] = harvest_source["organization"]["id"] \
        if isinstance(harvest_source.get("organization"), dict) else harvest_source["owner_org"]
    package_dict["type"] = "dataset"

    package = toolkit.get_action("package_create")(dict(context), package_dict)

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
