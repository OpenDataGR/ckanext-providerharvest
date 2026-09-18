"""Coverage for the "richer approval-workflow UI" Phase 2 piece:
provider_source_reject (the counterpart to _activate), provider_source_
pause/_resume (self-service org-admin, or a sysadmin via CKAN's own
check_access short-circuit), and provider_source_list_all (the admin
dashboard backing admin_all_sources.html). Exercises the actions
directly, at the same level test_e2e_harvest.py does -- the routes that
call these are covered separately in test_blueprint.py.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

MOCK_PROVIDER_URL = "http://mock-provider:8080/records"


@pytest.fixture(autouse=True)
def _allow_private_network(monkeypatch):
    import ckanext.providerharvest.transport.direct_https as transport_mod
    monkeypatch.setattr(
        transport_mod, "assert_safe_http_url", lambda url, allow_private_ranges=False: None
    )


def _row_rules():
    return [
        {"ckan_field": "id", "source_path": "id", "field_type": "integer", "is_primary_key": True},
    ]


def _dataset_defaults(name: str):
    return {
        "title_translated": {"en": name},
        "notes_translated": {"en": "Approval-workflow integration test dataset for %s" % name},
    }


_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


def _register(editor_ctx, org, name):
    return helpers.call_action(
        "provider_source_create", editor_ctx,
        name=name,
        owner_org=org["id"],
        endpoint_url=MOCK_PROVIDER_URL,
        transport_type="http",
        auth_type="api_key",
        credential_fields={"api_key": "test-key"},
        row_rules=_row_rules(),
        pagination={"style": "page_number", "items_path": "results"},
        frequency="MANUAL",
        dataset_defaults=_dataset_defaults(name),
    )


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestProviderSourceReject:
    def test_sysadmin_rejects_a_pending_source(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}

        created = _register(editor_ctx, org, "reject-source")
        result = helpers.call_action(
            "provider_source_reject", {"user": sysadmin["name"]},
            harvest_source_id=created["harvest_source_id"], reason="Endpoint looks unreachable.",
        )
        assert result["status"] == "rejected"

        from ckanext.providerharvest.model import provider_source as provider_source_model
        provider_source = provider_source_model.get_by_harvest_source_id(created["harvest_source_id"])
        assert provider_source.status == "rejected"
        assert provider_source.rejection_reason == "Endpoint looks unreachable."

        # Shows up on the provider's own list with the reason attached.
        mine = helpers.call_action("provider_source_list_mine", editor_ctx)
        rejected = next(s for s in mine if s["id"] == created["harvest_source_id"])
        assert rejected["provider_status"] == "rejected"
        assert rejected["rejection_reason"] == "Endpoint looks unreachable."

    def test_org_editor_cannot_reject_their_own_source(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        created = _register(editor_ctx, org, "reject-denied-source")

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_reject", editor_ctx,
                harvest_source_id=created["harvest_source_id"],
            )

    def test_cannot_reject_an_already_active_source(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}

        created = _register(editor_ctx, org, "reject-active-source")
        helpers.call_action(
            "provider_source_activate", sysadmin_ctx, harvest_source_id=created["harvest_source_id"]
        )

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_reject", sysadmin_ctx,
                harvest_source_id=created["harvest_source_id"],
            )


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestProviderSourcePauseResume:
    def _activated_source(self, org, editor_ctx, sysadmin_ctx, name):
        created = _register(editor_ctx, org, name)
        helpers.call_action(
            "provider_source_activate", sysadmin_ctx, harvest_source_id=created["harvest_source_id"]
        )
        return created["harvest_source_id"]

    def test_org_admin_pauses_and_resumes_their_own_source(self):
        org = factories.Organization()
        editor = factories.User()
        org_admin = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=org_admin["name"], role="admin",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}
        admin_ctx = {"user": org_admin["name"], "ignore_auth": False}

        harvest_source_id = self._activated_source(org, editor_ctx, sysadmin_ctx, "pause-source")

        paused = helpers.call_action(
            "provider_source_pause", admin_ctx, harvest_source_id=harvest_source_id
        )
        assert paused["status"] == "paused"

        resumed = helpers.call_action(
            "provider_source_resume", admin_ctx, harvest_source_id=harvest_source_id
        )
        assert resumed["status"] == "active"

    def test_org_editor_cannot_pause_only_org_admin_can(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}

        harvest_source_id = self._activated_source(org, editor_ctx, sysadmin_ctx, "pause-denied-source")

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_pause", editor_ctx, harvest_source_id=harvest_source_id
            )

    def test_paused_source_is_skipped_by_gather_stage(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}

        harvest_source_id = self._activated_source(org, editor_ctx, sysadmin_ctx, "pause-skip-source")
        helpers.call_action(
            "provider_source_pause", sysadmin_ctx, harvest_source_id=harvest_source_id
        )

        from ckanext.harvest.model import HarvestJob, HarvestSource
        from ckanext.providerharvest.harvesters.base_generic import GenericProviderHarvester
        harvest_source_orm = HarvestSource.get(harvest_source_id)
        job = HarvestJob(source=harvest_source_orm)
        job.save()

        object_ids = GenericProviderHarvester().gather_stage(job)
        assert object_ids == [], "a paused source must not be harvested"

    def test_cannot_pause_a_pending_source(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        created = _register({"user": editor["name"], "ignore_auth": False}, org, "pause-pending-source")

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_pause", {"user": sysadmin["name"]},
                harvest_source_id=created["harvest_source_id"],
            )

    def test_cannot_resume_an_active_source(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}
        harvest_source_id = self._activated_source(org, editor_ctx, sysadmin_ctx, "resume-active-source")

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_resume", sysadmin_ctx, harvest_source_id=harvest_source_id
            )


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestProviderSourceListAll:
    def test_sysadmin_sees_every_status(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}
        sysadmin_ctx = {"user": sysadmin["name"]}

        pending_id = _register(editor_ctx, org, "list-all-pending")["harvest_source_id"]
        rejected_id = _register(editor_ctx, org, "list-all-rejected")["harvest_source_id"]
        helpers.call_action(
            "provider_source_reject", sysadmin_ctx, harvest_source_id=rejected_id
        )

        results = helpers.call_action("provider_source_list_all", sysadmin_ctx)
        statuses_by_id = {r["harvest_source_id"]: r["status"] for r in results}
        assert statuses_by_id[pending_id] == "pending"
        assert statuses_by_id[rejected_id] == "rejected"

    def test_non_sysadmin_cannot_list_all(self):
        editor = factories.User()
        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_list_all", {"user": editor["name"], "ignore_auth": False}
            )
