"""Regression test for a real 500 error found via the live preview
workflow: /provider-harvest/sources/test-connection crashed for every
HTTP source (not just this one endpoint) because
test_connection_result.html checked `result.sample_files is not none`
to decide whether to render the SFTP/FTP branch or the HTTP one. For an
HTTP source, `result` has no `sample_files` key at all (only
`sample_records`/`preview_rows`) -- Jinja's dot-access on a *missing*
dict key returns its own `Undefined` sentinel, not Python's `None`, and
`Undefined is not none` is True (they're different objects), so the
template took the wrong branch and then tried to JSON-serialize the
Undefined object itself, which isn't serializable. Fixed by using
`result.get('sample_files')` (a real dict method, returns real `None`
for a missing key) instead of attribute-style access.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestConnectionResultRendersForHttpSources:
    def test_test_connection_result_page_renders_for_http(self, app):
        """Any real public endpoint reproduces this -- the bug is in the
        template's branching, not anything endpoint-specific. Uses
        jsonplaceholder.typicode.com (reachable from CI, a free public
        test API) with cursor-style pagination and the form's own
        pre-filled default items_path ("results"), matching exactly what
        was actually submitted when this was first found."""
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
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Connected successfully" in body
        assert "Sample records (raw)" in body
        assert "Matching files on the server" not in body
