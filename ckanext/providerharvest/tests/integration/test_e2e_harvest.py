"""End-to-end harvest-job scenario against the docker/ replica, per
DESIGN.md's "Verification" section: register a mock HTTP provider, run a
job, confirm rows land in DataStore -- plus the org-scoping checks noted
there. This is the piece that was still "not yet scripted" per README.md.

Runs for real against the live stack: the mock-provider HTTP service
(docker/mock-provider/serve.py), the already-running ckan-worker queue
consumers picking up the harvest job over Redis, and Postgres/DataStore --
nothing here is mocked at the Python level except the network-target
validator (see _allow_private_network below).
"""

from __future__ import annotations

import time

import pytest
from ckan.tests import factories, helpers

MOCK_PROVIDER_URL = "http://mock-provider:8080/records"


@pytest.fixture(autouse=True)
def _allow_private_network(monkeypatch):
    # The real network-target validator rejects RFC1918/container-network
    # addresses by design (SSRF defense for real provider registrations,
    # which are expected to be public-internet + IP-allowlisted per
    # DESIGN.md) -- mock-provider's compose-network address would
    # otherwise be rejected. Bypassed here the same way the transport's
    # own unit tests do (test_transport_direct_https.py's patch_resolver),
    # not by loosening anything in the actual extension code.
    import ckanext.providerharvest.transport.direct_https as transport_mod
    monkeypatch.setattr(
        transport_mod, "assert_safe_http_url", lambda url, allow_private_ranges=False: None
    )


def _row_rules():
    return [
        {"ckan_field": "id", "source_path": "id", "field_type": "integer", "is_primary_key": True},
        {"ckan_field": "name", "source_path": "name", "field_type": "text"},
    ]


def _dataset_defaults(name: str):
    # scheming_datasets' bundled schema (a stand-in for data.gov.gr's real
    # DCAT-AP-EL one, see docker/.env.example) only requires the
    # multilingual title/notes fields beyond stock CKAN fields.
    return {
        "title_translated": {"en": name},
        "notes_translated": {"en": "Integration test dataset for %s" % name},
    }


def _wait_for_datastore_rows(resource_id: str, expected_count: int, timeout_s: float = 60):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = helpers.call_action("datastore_search", {}, resource_id=resource_id)
        if len(last["records"]) >= expected_count:
            return last
        time.sleep(2)
    raise AssertionError(
        "DataStore never reached %d row(s) for resource %s within %ss (last result: %r)"
        % (expected_count, resource_id, timeout_s, last)
    )


#: pytest-ckan's `with_plugins` fixture loads exactly this list for the
#: test's own app/config context -- separate from whatever was already
#: loaded when the `ckan` CLI process first booted pytest itself, which
#: is why `provider_source_create` came back as "Action ... not found"
#: without this even though the container's own ckan.ini has all of these
#: active. Mirrors CKAN__PLUGINS in docker/.env.example.
_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


#: Deliberately no clean_db fixture here. It drops every table and
#: recreates only CKAN core's + Alembic-migrated extensions' (e.g.
#: ckanext-harvest's own) -- this extension's own tables AND
#: ckanext-datastore's internal `_table_metadata` view are both created
#: once at process startup, outside that migration chain, so clean_db
#: breaks datastore entirely for any test after it runs (confirmed:
#: UndefinedTable on `_table_metadata` the moment xloader's
#: after_resource_update hook calls datastore_info). Not needed anyway:
#: each test below uses distinct org/user/source names, and the
#: container's own initial boot already leaves a clean, fully-set-up DB.
@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestFullHarvestFlow:
    def test_register_activate_run_and_query_datastore(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()

        # ignore_auth explicitly False throughout this test: it's the
        # whole point of exercising provider_source_create/_activate as
        # an actual org editor rather than the test-helper default (see
        # the note by the pytest.raises block below).
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        created = helpers.call_action(
            "provider_source_create",
            editor_ctx,
            name="mock-provider-source",
            owner_org=org["id"],
            endpoint_url=MOCK_PROVIDER_URL,
            transport_type="http",
            auth_type="api_key",
            credential_fields={"api_key": "test-key"},
            row_rules=_row_rules(),
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("mock-provider-source"),
        )
        harvest_source_id = created["harvest_source_id"]
        assert created["status"] == "pending"

        # The approval gate: an org editor cannot self-activate.
        # ckan.tests.helpers.call_action defaults ignore_auth to True
        # unless the context says otherwise -- without editor_ctx setting
        # it False above, this check never actually runs,
        # provider_source_activate executes for real, and the
        # *sysadmin's* call further down then collides with the packages
        # this "denied" call already created.
        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_activate", editor_ctx, harvest_source_id=harvest_source_id
            )

        sysadmin_ctx = {"user": sysadmin["name"]}
        activated = helpers.call_action(
            "provider_source_activate", sysadmin_ctx, harvest_source_id=harvest_source_id
        )
        assert activated["status"] == "active"

        mine = helpers.call_action("provider_source_list_mine", editor_ctx)
        assert any(s["id"] == harvest_source_id for s in mine)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        provider_source = provider_source_model.get_by_harvest_source_id(harvest_source_id)
        assert provider_source.ckan_resource_id

        # Enqueue a real harvest job -- picked up by the already-running
        # ckan-worker gather/fetch consumers (see docker/ckan/worker-entrypoint.sh),
        # exactly like a scheduled run in production would be.
        helpers.call_action(
            "harvest_job_create", sysadmin_ctx, source_id=harvest_source_id
        )

        result = _wait_for_datastore_rows(provider_source.ckan_resource_id, expected_count=3)
        records_by_id = {r["id"]: r["name"] for r in result["records"]}
        assert records_by_id == {1: "Alpha", 2: "Bravo", 3: "Charlie"}

    def test_org_scoping_hides_other_orgs_sources(self):
        org_a = factories.Organization()
        org_b = factories.Organization()
        editor_a = factories.User()
        editor_b = factories.User()
        for org, editor in ((org_a, editor_a), (org_b, editor_b)):
            helpers.call_action(
                "organization_member_create",
                {"ignore_auth": True},
                id=org["id"], username=editor["name"], role="editor",
            )

        created = helpers.call_action(
            "provider_source_create",
            {"user": editor_a["name"], "ignore_auth": False},
            name="org-a-source",
            owner_org=org_a["id"],
            endpoint_url=MOCK_PROVIDER_URL,
            transport_type="http",
            auth_type="api_key",
            credential_fields={"api_key": "test-key"},
            row_rules=_row_rules(),
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("org-a-source"),
        )

        mine_b = helpers.call_action(
            "provider_source_list_mine", {"user": editor_b["name"]}
        )
        assert all(s["id"] != created["harvest_source_id"] for s in mine_b)

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_test_connection",
                {"user": editor_b["name"], "ignore_auth": False},
                owner_org=org_a["id"],
                endpoint_url=MOCK_PROVIDER_URL,
                transport_type="http",
                credential_fields={"api_key": "test-key"},
            )
