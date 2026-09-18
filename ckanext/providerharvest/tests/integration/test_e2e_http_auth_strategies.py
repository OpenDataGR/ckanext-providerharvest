"""End-to-end coverage for the HTTP AuthStrategy implementations added
alongside ApiKeyAuth -- BasicAuth and OAuth2ClientCredentialsAuth --
against the real mock-provider container (see docker/mock-provider/serve.py's
/records-basic and /records-bearer + /oauth/token endpoints), not just the
fakes in test_auth_strategies.py. MTLSAuth isn't exercised here -- see
that server file's own docstring for why (no CA the test environment
would also trust to present, and the unit tests already cover the real
logic directly).

Same reasoning as test_e2e_harvest.py's module docstring for calling the
harvester stages directly rather than via the real queue, and for the
_allow_private_network fixture.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

from ckanext.providerharvest.harvesters.base_generic import GenericProviderHarvester

MOCK_PROVIDER_BASIC_URL = "http://mock-provider:8080/records-basic"
MOCK_PROVIDER_BEARER_URL = "http://mock-provider:8080/records-bearer"
MOCK_OAUTH2_TOKEN_URL = "http://mock-provider:8080/oauth/token"


@pytest.fixture(autouse=True)
def _allow_private_network(monkeypatch):
    import ckanext.providerharvest.transport.direct_https as transport_mod
    monkeypatch.setattr(
        transport_mod, "assert_safe_http_url", lambda url, allow_private_ranges=False: None
    )
    import ckanext.providerharvest.auth_strategies.oauth2_client_credentials as oauth2_mod
    monkeypatch.setattr(
        oauth2_mod, "assert_safe_http_url", lambda url, allow_private_ranges=False: None
    )


def _row_rules():
    return [
        {"ckan_field": "id", "source_path": "id", "field_type": "integer", "is_primary_key": True},
        {"ckan_field": "name", "source_path": "name", "field_type": "text"},
    ]


def _dataset_defaults(name: str):
    return {
        "title_translated": {"en": name},
        "notes_translated": {"en": "HTTP auth-strategy integration test dataset for %s" % name},
    }


_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestHttpAuthStrategies:
    def _register_activate_and_run(self, editor_ctx, sysadmin_ctx, **create_kwargs):
        created = helpers.call_action("provider_source_create", editor_ctx, **create_kwargs)
        harvest_source_id = created["harvest_source_id"]
        assert created["status"] == "pending"

        activated = helpers.call_action(
            "provider_source_activate", sysadmin_ctx, harvest_source_id=harvest_source_id
        )
        assert activated["status"] == "active"

        from ckanext.providerharvest.model import provider_source as provider_source_model
        provider_source = provider_source_model.get_by_harvest_source_id(harvest_source_id)

        from ckanext.harvest.model import HarvestJob, HarvestSource
        harvest_source_orm = HarvestSource.get(harvest_source_id)
        job = HarvestJob(source=harvest_source_orm)
        job.save()

        harvester = GenericProviderHarvester()
        object_ids = harvester.gather_stage(job)
        assert object_ids, "gather_stage produced no harvest objects"

        from ckanext.harvest.model import HarvestObject
        for object_id in object_ids:
            harvest_object = HarvestObject.get(object_id)
            assert harvester.fetch_stage(harvest_object)
            assert harvester.import_stage(harvest_object), harvest_object.errors

        return provider_source

    def test_basic_auth_end_to_end(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}

        provider_source = self._register_activate_and_run(
            editor_ctx, sysadmin_ctx,
            name="basic-auth-source",
            owner_org=org["id"],
            endpoint_url=MOCK_PROVIDER_BASIC_URL,
            transport_type="http",
            auth_type="basic_auth",
            credential_fields={"username": "mock-user", "password": "mock-pass"},
            row_rules=_row_rules(),
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("basic-auth-source"),
        )

        result = helpers.call_action(
            "datastore_search", {}, resource_id=provider_source.ckan_resource_id
        )
        records_by_id = {r["id"]: r["name"] for r in result["records"]}
        assert records_by_id == {1: "Alpha", 2: "Bravo", 3: "Charlie"}

    def test_basic_auth_wrong_credentials_fails_the_job(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}

        created = helpers.call_action(
            "provider_source_create", editor_ctx,
            name="basic-auth-wrong-source",
            owner_org=org["id"],
            endpoint_url=MOCK_PROVIDER_BASIC_URL,
            transport_type="http",
            auth_type="basic_auth",
            credential_fields={"username": "mock-user", "password": "not-the-right-password"},
            row_rules=_row_rules(),
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("basic-auth-wrong-source"),
        )
        helpers.call_action(
            "provider_source_activate", {"user": sysadmin["name"]},
            harvest_source_id=created["harvest_source_id"],
        )

        from ckanext.harvest.model import HarvestJob, HarvestSource
        harvest_source_orm = HarvestSource.get(created["harvest_source_id"])
        job = HarvestJob(source=harvest_source_orm)
        job.save()

        object_ids = GenericProviderHarvester().gather_stage(job)
        assert object_ids == [], "wrong Basic auth credentials should reject at the provider"

    def test_oauth2_client_credentials_end_to_end(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}

        provider_source = self._register_activate_and_run(
            editor_ctx, sysadmin_ctx,
            name="oauth2-source",
            owner_org=org["id"],
            endpoint_url=MOCK_PROVIDER_BEARER_URL,
            transport_type="http",
            auth_type="oauth2_client_credentials",
            credential_fields={"client_id": "mock-client", "client_secret": "mock-secret"},
            auth_opts={"token_url": MOCK_OAUTH2_TOKEN_URL},
            row_rules=_row_rules(),
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("oauth2-source"),
        )

        result = helpers.call_action(
            "datastore_search", {}, resource_id=provider_source.ckan_resource_id
        )
        records_by_id = {r["id"]: r["name"] for r in result["records"]}
        assert records_by_id == {1: "Alpha", 2: "Bravo", 3: "Charlie"}
