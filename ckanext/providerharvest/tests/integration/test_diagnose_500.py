"""Temporary diagnostic test -- reproduces the exact scenario reported
from the live preview: registering transport_type=http against a real
public JSON API (jsonplaceholder, reachable from the CI runner but not
from the dev machine used to investigate this, which has broken local
TLS trust for outbound HTTPS entirely) with pagination.style=cursor and
items_path left at its default "results" value (the form's pre-filled
default, not cleared to "$" -- jsonplaceholder's /users returns a raw
JSON array, not a "results"-wrapped envelope). Calling
provider_source_test_connection through the web route produced an
Internal Server Error (500) in the browser. To be deleted once the real
cause is found and fixed.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestDiagnose500:
    def test_reproduce_via_action_directly(self):
        org = factories.Organization()
        sysadmin = factories.Sysadmin()
        result = helpers.call_action(
            "provider_source_test_connection",
            {"user": sysadmin["name"]},
            owner_org=org["id"],
            endpoint_url="https://jsonplaceholder.typicode.com/users",
            transport_type="http",
            auth_type="api_key",
            credential_fields={"api_key": "not-needed"},
            pagination={"style": "cursor", "items_path": "results", "page_param": "page"},
        )
        print("ACTION RESULT:", result)

    def test_reproduce_via_web_route(self, app):
        org = factories.Organization()
        sysadmin = factories.Sysadmin()
        resp = app.post(
            "/provider-harvest/sources/test-connection",
            extra_environ={"REMOTE_USER": sysadmin["name"]},
            data={
                "name": "jsonplaceholder-demo",
                "owner_org": org["id"],
                "transport_type": "http",
                "endpoint_url": "https://jsonplaceholder.typicode.com/users",
                "auth_type": "api_key",
                "api_key": "not-needed",
                "api_key_location": "header",
                "api_key_name": "X-API-Key",
                "pagination_style": "cursor",
                "items_path": "results",
                "page_param": "page",
                "row_rules-0-ckan_field": "id",
                "row_rules-0-source_path": "id",
                "row_rules-0-field_type": "integer",
                "row_rules-0-is_primary_key": "on",
                "row_rules-1-ckan_field": "name",
                "row_rules-1-source_path": "name",
                "row_rules-1-field_type": "text",
            },
            follow_redirects=False,
        )
        print("STATUS:", resp.status_code)
        print("BODY:", resp.get_data(as_text=True)[:3000])
        assert resp.status_code == 200, resp.get_data(as_text=True)
