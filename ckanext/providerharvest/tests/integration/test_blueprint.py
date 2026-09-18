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

    def test_scp_register_via_web_form_through_the_ssh_subsystem_selector(
        self, app, _allow_private_network
    ):
        """Unlike the SFTP test above (which posts transport_type=sftp
        directly), this one goes through the real top-level selector
        value ("ssh") plus the SSH-subsystem sub-selector ("scp") the
        template actually renders -- see
        provider_ui._resolve_transport_type -- against the real
        scp-provider container (not atmoz/sftp; see
        docker/scp-provider/Dockerfile for why)."""
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
            "endpoint_url": "sftp://scp-provider",
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
                "name": "ui-scp-test-source",
                "title": "UI SCP Test Source",
                "owner_org": org["id"],
                "transport_type": "ssh",
                "ssh_subsystem": "scp",
                "endpoint_url": "sftp://scp-provider",
                "sftp_port": "22",
                "sftp_remote_path": "/home/produser/upload",
                "sftp_glob_pattern": "*.csv",
                "sftp_username": "produser",
                "sftp_password": "test-pass",
                "host_key_fingerprint": fingerprint,
                "dataset_title": "UI SCP Test Dataset",
                "dataset_notes": "Created by the blueprint SCP smoke test.",
                "frequency": "MANUAL",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200), resp.get_data(as_text=True)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        from ckan.model import Package, Session
        pkg = Session.query(Package).filter_by(name="ui-scp-test-source", type="harvest").first()
        assert pkg is not None, "provider_source_create was never actually called"
        provider_source = provider_source_model.get_by_harvest_source_id(pkg.id)
        assert provider_source is not None
        assert provider_source.status == "pending"
        assert provider_source.host_key_fingerprint == fingerprint

        harvest_source = helpers.call_action(
            "harvest_source_show", {"ignore_auth": True}, id=pkg.id
        )
        import json
        assert json.loads(harvest_source["config"])["transport_type"] == "scp"

    def test_ftp_register_via_web_form_with_plain_ftp_acknowledgment(
        self, app, _allow_private_network
    ):
        """The FTP half of the self-service form: use_tls unchecked plus
        the explicit acknowledgment checkbox, against the real
        ftp-provider container (plain FTP only, see
        docker/ftp-provider/Dockerfile)."""
        org = factories.Organization()
        editor = factories.User()
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
                "name": "ui-ftp-test-source",
                "title": "UI FTP Test Source",
                "owner_org": org["id"],
                "transport_type": "ftp",
                "endpoint_url": "ftp://ftp-provider",
                "ftp_port": "21",
                "ftp_remote_path": "/upload",
                "ftp_glob_pattern": "*.csv",
                "ftp_username": "produser",
                "ftp_password": "test-pass",
                # ftp_use_tls deliberately omitted (unchecked) --
                # plain_ftp_acknowledged is what makes that acceptable.
                "ftp_plain_ftp_acknowledged": "on",
                "dataset_title": "UI FTP Test Dataset",
                "dataset_notes": "Created by the blueprint FTP smoke test.",
                "frequency": "MANUAL",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200), resp.get_data(as_text=True)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        from ckan.model import Package, Session
        pkg = Session.query(Package).filter_by(name="ui-ftp-test-source", type="harvest").first()
        assert pkg is not None, "provider_source_create was never actually called"
        provider_source = provider_source_model.get_by_harvest_source_id(pkg.id)
        assert provider_source is not None
        assert provider_source.status == "pending"
        assert provider_source.plain_ftp_acknowledged is True

        harvest_source = helpers.call_action(
            "harvest_source_show", {"ignore_auth": True}, id=pkg.id
        )
        import json
        config = json.loads(harvest_source["config"])
        assert config["transport_type"] == "ftp"
        assert config["use_tls"] is False

    def test_http_basic_auth_register_via_web_form(self, app):
        """The HTTP "Authentication" sub-selector added alongside
        BasicAuth/OAuth2ClientCredentialsAuth/MTLSAuth -- catches any
        field-name mismatch between source_form.html and
        provider_ui._http_auth_payload before it reaches real credential
        storage. No network call happens during registration itself
        (only during test-connection/a real harvest run), so this
        doesn't need the private-network allowance the SFTP/SCP/FTP
        tests do."""
        org = factories.Organization()
        editor = factories.User()
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
                "name": "ui-basic-auth-test-source",
                "title": "UI Basic Auth Test Source",
                "owner_org": org["id"],
                "transport_type": "http",
                "endpoint_url": "https://provider.example.com/api/records",
                "auth_type": "basic_auth",
                "basic_username": "alice",
                "basic_password": "s3cret",
                "pagination_style": "page_number",
                "items_path": "results",
                "page_param": "page",
                "row_rules-0-ckan_field": "id",
                "row_rules-0-source_path": "id",
                "row_rules-0-field_type": "integer",
                "row_rules-0-is_primary_key": "on",
                "dataset_title": "UI Basic Auth Test Dataset",
                "dataset_notes": "Created by the blueprint basic-auth smoke test.",
                "frequency": "MANUAL",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200), resp.get_data(as_text=True)

        from ckan.model import Package, Session
        pkg = Session.query(Package).filter_by(
            name="ui-basic-auth-test-source", type="harvest"
        ).first()
        assert pkg is not None, "provider_source_create was never actually called"

        harvest_source = helpers.call_action(
            "harvest_source_show", {"ignore_auth": True}, id=pkg.id
        )
        import json
        config = json.loads(harvest_source["config"])
        assert config["transport_type"] == "http"
        assert config["auth_type"] == "basic_auth"

    def test_new_source_form_has_the_dynamic_row_rules_editor(self, app):
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
        body = resp.get_data(as_text=True)
        assert 'id="row-rules-template"' in body
        assert 'id="row-rules-add"' in body
        assert "row_rules-__INDEX__-ckan_field" in body

    def test_row_rules_with_a_gap_in_indices_all_parse(self, app):
        """Simulates what a JS "remove middle row" leaves behind: indices
        0 and 2 present, 1 missing -- provider_ui._row_rules_from_form
        must not stop at the first gap (a fixed-slots form never needed
        to handle this; the dynamic editor does)."""
        org = factories.Organization()
        editor = factories.User()
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
                "name": "ui-gap-rows-source",
                "title": "UI Gap Rows Source",
                "owner_org": org["id"],
                "transport_type": "http",
                "endpoint_url": "https://provider.example.com/api/records",
                "auth_type": "api_key",
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
                # row_rules-1-* deliberately absent (removed client-side).
                "row_rules-2-ckan_field": "name",
                "row_rules-2-source_path": "name",
                "row_rules-2-field_type": "text",
                "dataset_title": "UI Gap Rows Dataset",
                "dataset_notes": "Created by the gap-indices smoke test.",
                "frequency": "MANUAL",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200), resp.get_data(as_text=True)

        from ckan.model import Package, Session
        pkg = Session.query(Package).filter_by(
            name="ui-gap-rows-source", type="harvest"
        ).first()
        assert pkg is not None, "provider_source_create was never actually called"

        from ckanext.providerharvest.model import field_mapping as field_mapping_model
        profile = field_mapping_model.load_latest(pkg.id)
        assert sorted(r.ckan_field for r in profile.row_rules) == ["id", "name"]

    def test_admin_reject_via_web_form(self, app):
        org = factories.Organization()
        editor = factories.User()
        sysadmin = factories.Sysadmin()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        created = helpers.call_action(
            "provider_source_create", {"user": editor["name"], "ignore_auth": False},
            name="ui-reject-source", owner_org=org["id"],
            endpoint_url="https://provider.example.com/api/records",
            transport_type="http", auth_type="api_key",
            credential_fields={"api_key": "test-key"},
            row_rules=[{
                "ckan_field": "id", "source_path": "id",
                "field_type": "integer", "is_primary_key": True,
            }],
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults={
                "title_translated": {"en": "UI Reject Source"},
                "notes_translated": {"en": "For the reject-route smoke test."},
            },
        )

        sysadmin_environ = {"REMOTE_USER": sysadmin["name"]}
        resp = app.post(
            "/provider-harvest/admin/sources/%s/reject" % created["harvest_source_id"],
            extra_environ=sysadmin_environ,
            data={"reason": "Not a real provider."},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        provider_source = provider_source_model.get_by_harvest_source_id(created["harvest_source_id"])
        assert provider_source.status == "rejected"
        assert provider_source.rejection_reason == "Not a real provider."

    def test_admin_all_sources_page_and_pause_resume_via_web_form(self, app):
        org = factories.Organization()
        editor = factories.User()
        org_admin = factories.User()
        sysadmin = factories.Sysadmin()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=org_admin["name"], role="admin",
        )
        created = helpers.call_action(
            "provider_source_create", {"user": editor["name"], "ignore_auth": False},
            name="ui-pause-source", owner_org=org["id"],
            endpoint_url="https://provider.example.com/api/records",
            transport_type="http", auth_type="api_key",
            credential_fields={"api_key": "test-key"},
            row_rules=[{
                "ckan_field": "id", "source_path": "id",
                "field_type": "integer", "is_primary_key": True,
            }],
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults={
                "title_translated": {"en": "UI Pause Source"},
                "notes_translated": {"en": "For the pause/resume-route smoke test."},
            },
        )
        sysadmin_environ = {"REMOTE_USER": sysadmin["name"]}
        helpers.call_action(
            "provider_source_activate", {"user": sysadmin["name"]},
            harvest_source_id=created["harvest_source_id"],
        )

        all_sources_resp = app.get(
            "/provider-harvest/admin/sources", extra_environ=sysadmin_environ
        )
        assert all_sources_resp.status_code == 200
        assert "UI Pause Source" in all_sources_resp.get_data(as_text=True)

        # Self-service pause, as the owning org's admin (not the editor
        # who registered it, not a sysadmin).
        admin_environ = {"REMOTE_USER": org_admin["name"]}
        pause_resp = app.post(
            "/provider-harvest/sources/%s/pause" % created["harvest_source_id"],
            extra_environ=admin_environ,
            follow_redirects=False,
        )
        assert pause_resp.status_code in (302, 200)

        from ckanext.providerharvest.model import provider_source as provider_source_model
        provider_source = provider_source_model.get_by_harvest_source_id(created["harvest_source_id"])
        assert provider_source.status == "paused"

        resume_resp = app.post(
            "/provider-harvest/sources/%s/resume" % created["harvest_source_id"],
            extra_environ=admin_environ,
            follow_redirects=False,
        )
        assert resume_resp.status_code in (302, 200)

        provider_source = provider_source_model.get_by_harvest_source_id(created["harvest_source_id"])
        assert provider_source.status == "active"

    def test_org_editor_pause_button_is_not_rendered_but_route_still_denies(self, app):
        """source_list.html only shows Pause/Resume to org admins (see
        _admin_org_ids_for_current_user) -- confirm the route itself
        still enforces that even if a plain editor posts to it directly."""
        org = factories.Organization()
        editor = factories.User()
        sysadmin = factories.Sysadmin()
        helpers.call_action(
            "organization_member_create", {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        created = helpers.call_action(
            "provider_source_create", {"user": editor["name"], "ignore_auth": False},
            name="ui-pause-denied-source", owner_org=org["id"],
            endpoint_url="https://provider.example.com/api/records",
            transport_type="http", auth_type="api_key",
            credential_fields={"api_key": "test-key"},
            row_rules=[{
                "ckan_field": "id", "source_path": "id",
                "field_type": "integer", "is_primary_key": True,
            }],
            pagination={"style": "page_number", "items_path": "results"},
            frequency="MANUAL",
            dataset_defaults={
                "title_translated": {"en": "UI Pause Denied Source"},
                "notes_translated": {"en": "For the pause-denied smoke test."},
            },
        )
        helpers.call_action(
            "provider_source_activate", {"user": sysadmin["name"]},
            harvest_source_id=created["harvest_source_id"],
        )

        editor_environ = {"REMOTE_USER": editor["name"]}
        list_resp = app.get("/provider-harvest/sources", extra_environ=editor_environ)
        assert "Pause" not in list_resp.get_data(as_text=True)

        pause_resp = app.post(
            "/provider-harvest/sources/%s/pause" % created["harvest_source_id"],
            extra_environ=editor_environ,
        )
        assert pause_resp.status_code == 403
