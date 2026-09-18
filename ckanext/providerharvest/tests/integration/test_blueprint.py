"""Smoke tests for the self-service web UI (blueprints/provider_ui.py) --
the Phase 2 piece added after the harvest-job integration test proved the
underlying actions work. Exercises the actual Flask routes/templates
against the real replica, not just the actions directly, since template
rendering (page.html block names, CKAN Jinja2 helpers) is exactly the
kind of thing that only breaks when actually rendered.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

#: Same list (and same reasoning) as test_e2e_harvest.py's
#: _REQUIRED_PLUGINS -- with_plugins needs this to actually load
#: providerharvest for this test's own app/config context.
_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


@pytest.fixture
def _allow_private_network(monkeypatch):
    # Same reasoning as test_e2e_sftp_harvest.py: sftp-provider's
    # compose-network address is exactly what the real SSRF-class
    # validator exists to reject for a real registration. Patched in
    # ssh_common (shared by SFTPTransport/ScpTransport) -- see
    # transport/ssh_common.py.
    import ckanext.providerharvest.transport.ssh_common as ssh_common_mod
    monkeypatch.setattr(
        ssh_common_mod, "assert_safe_network_target",
        lambda host, port, allow_private_ranges=False: [host],
    )


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestProviderUIBlueprint:
    def test_sources_requires_login(self, app):
        resp = app.get("/provider-harvest/sources", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_new_source_form_renders_for_org_editor(self, app):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )

        resp = app.get(
            "/provider-harvest/sources/new",
            extra_environ={"REMOTE_USER": editor["name"]},
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Register a provider source" in body
        assert org["name"] in body or org["id"] in body

    def test_register_list_and_admin_activate_flow(self, app):
        org = factories.Organization()
        editor = factories.User()
        sysadmin = factories.Sysadmin()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        editor_environ = {"REMOTE_USER": editor["name"]}

        resp = app.post(
            "/provider-harvest/sources/new",
            extra_environ=editor_environ,
            data={
                "name": "ui-test-source",
                "title": "UI Test Source",
                "owner_org": org["id"],
                "endpoint_url": "https://provider.example.com/api/records",
                "api_key": "test-key",
                "api_key_location": "header",
                "api_key_name": "X-API-Key",
                "pagination_style": "page_number",
                "items_path": "results",
                "page_param": "page",
                "row_rules-0-ckan_field": "id",
                "row_rules-0-source_path": "id",
                "row_rules-0-field_type": "integer",
                "row_rules-0-is_primary_key": "on",
                "row_rules-1-ckan_field": "name",
                "row_rules-1-source_path": "name",
                "row_rules-1-field_type": "text",
                "dataset_title": "UI Test Dataset",
                "dataset_notes": "Created by the blueprint smoke test.",
                "frequency": "MANUAL",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        from ckan.model import Package, Session
        pkg = Session.query(Package).filter_by(name="ui-test-source", type="harvest").first()
        assert pkg is not None, "provider_source_create was never actually called"
        provider_source = provider_source_model.get_by_harvest_source_id(pkg.id)
        assert provider_source is not None
        assert provider_source.status == "pending"

        list_resp = app.get("/provider-harvest/sources", extra_environ=editor_environ)
        assert list_resp.status_code == 200
        assert "UI Test Source" in list_resp.get_data(as_text=True) or \
            "ui-test-source" in list_resp.get_data(as_text=True)

        # A non-sysadmin can't reach the approval queue.
        denied = app.get(
            "/provider-harvest/admin/pending", extra_environ=editor_environ
        )
        assert denied.status_code == 403

        sysadmin_environ = {"REMOTE_USER": sysadmin["name"]}
        pending_resp = app.get(
            "/provider-harvest/admin/pending", extra_environ=sysadmin_environ
        )
        assert pending_resp.status_code == 200
        assert "UI Test Source" in pending_resp.get_data(as_text=True)

        activate_resp = app.post(
            "/provider-harvest/admin/sources/%s/activate" % pkg.id,
            extra_environ=sysadmin_environ,
            follow_redirects=False,
        )
        assert activate_resp.status_code in (302, 200)

        provider_source = provider_source_model.get_by_harvest_source_id(pkg.id)
        assert provider_source.status == "active"

    def test_sftp_register_via_web_form_with_host_key_fetch(self, app, _allow_private_network):
        """The SFTP half of the self-service form: fetch-host-key first
        (the trust-on-first-use step -- provider_source_create refuses a
        transport_type=sftp source without a pinned fingerprint), then
        register with it filled in, against the real sftp-provider
        container -- not a fake, exercising the actual template/route
        wiring added alongside SFTPTransport."""
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        editor_environ = {"REMOTE_USER": editor["name"]}
        base_fields = {
            "owner_org": org["id"],
            "endpoint_url": "sftp://sftp-provider",
            "sftp_port": "22",
        }

        fetch_resp = app.post(
            "/provider-harvest/sources/fetch-host-key",
            extra_environ=editor_environ,
            data=base_fields,
            follow_redirects=False,
        )
        assert fetch_resp.status_code == 200
        body = fetch_resp.get_data(as_text=True)
        assert "SHA256:" in body, "host key fingerprint was not filled into the re-rendered form"

        import re
        fingerprint = re.search(r"value=\"(SHA256:[^\"]+)\"", body).group(1)

        resp = app.post(
            "/provider-harvest/sources/new",
            extra_environ=editor_environ,
            data={
                "name": "ui-sftp-test-source",
                "title": "UI SFTP Test Source",
                "owner_org": org["id"],
                "transport_type": "sftp",
                "endpoint_url": "sftp://sftp-provider",
                "sftp_port": "22",
                "sftp_remote_path": "/upload",
                "sftp_glob_pattern": "*.csv",
                "sftp_username": "produser",
                "sftp_password": "test-pass",
                "host_key_fingerprint": fingerprint,
                "dataset_title": "UI SFTP Test Dataset",
                "dataset_notes": "Created by the blueprint SFTP smoke test.",
                "frequency": "MANUAL",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200), resp.get_data(as_text=True)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        from ckan.model import Package, Session
        pkg = Session.query(Package).filter_by(name="ui-sftp-test-source", type="harvest").first()
        assert pkg is not None, "provider_source_create was never actually called"
        provider_source = provider_source_model.get_by_harvest_source_id(pkg.id)
        assert provider_source is not None
        assert provider_source.status == "pending"
        assert provider_source.host_key_fingerprint == fingerprint

        list_resp = app.get("/provider-harvest/sources", extra_environ=editor_environ)
        assert "sftp" in list_resp.get_data(as_text=True)
