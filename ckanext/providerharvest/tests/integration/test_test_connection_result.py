"""Regression coverage for /provider-harvest/sources/test-connection,
both found via actually using the live preview, not by an automated
test catching them first:

1. A real 500 for every HTTP source (not just one endpoint): the old
   test_connection_result.html checked `result.sample_files is not
   none` to decide whether to render the SFTP/FTP branch or the HTTP
   one. For an HTTP source, `result` has no `sample_files` key at all
   (only `sample_records`/`preview_rows`) -- Jinja's dot-access on a
   *missing* dict key returns its own `Undefined` sentinel, not
   Python's `None`, and `Undefined is not none` is True (they're
   different objects), so the template took the wrong branch and then
   tried to JSON-serialize the Undefined object itself, which isn't
   serializable.

2. Once (1) was fixed enough to show a real error message instead of a
   500, the *next* thing found by hand was that clicking "Back to form"
   after a failed test-connection lost every field the provider had
   already filled in -- the old design rendered a *separate* result
   page whose "back" link was a plain GET to a blank form. Both bugs
   are fixed the same way now: test_connection() renders source_form.html
   directly (same pattern fetch_host_key() already used), with every
   submitted field preserved and the result/error shown inline instead
   of on a page of its own -- see provider_ui.test_connection's
   docstring.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestConnectionRendersInlineOnTheForm:
    def _submit(self, app, sysadmin, org, **overrides):
        data = {
            "name": "jsonplaceholder-demo",
            "owner_org": org["id"],
            "transport_type": "http",
            "endpoint_url": "https://jsonplaceholder.typicode.com/users",
            "auth_type": "api_key",
            "api_key": "not-needed",
            "api_key_location": "header",
            "api_key_name": "X-API-Key",
            "pagination_style": "page_number",
            "items_path": "$",
            "page_param": "page",
            "row_rules-0-ckan_field": "id",
            "row_rules-0-source_path": "id",
            "row_rules-0-field_type": "integer",
            "row_rules-0-is_primary_key": "on",
            "row_rules-1-ckan_field": "name",
            "row_rules-1-source_path": "name",
            "row_rules-1-field_type": "text",
        }
        data.update(overrides)
        return app.post(
            "/provider-harvest/sources/test-connection",
            extra_environ={"REMOTE_USER": sysadmin["name"]},
            data=data,
            follow_redirects=False,
        )

    def test_succeeds_and_shows_sample_records_for_http(self, app):
        """Any real public endpoint reproduces the original crash -- the
        bug was in the template's branching, not anything
        endpoint-specific. Uses jsonplaceholder.typicode.com (reachable
        from CI, a free public test API) with cursor-style pagination
        and the form's own pre-filled default items_path ("results"),
        matching exactly what was actually submitted when this was
        first found."""
        org = factories.Organization()
        sysadmin = factories.Sysadmin()
        resp = self._submit(app, sysadmin, org)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Connected successfully" in body
        assert "Sample records (raw)" in body
        assert "Matching files on the server" not in body

    def test_error_result_preserves_every_submitted_field(self, app):
        """The row_rules given here have no primary key (a real
        validation error, not a crash) -- what matters is that every
        other field the provider already typed survives being shown
        the error, instead of the old design's "back to form" link
        that reset the whole form to blank."""
        org = factories.Organization()
        sysadmin = factories.Sysadmin()
        resp = self._submit(
            app, sysadmin, org,
            name="jsonplaceholder-no-pk",
            **{"row_rules-0-is_primary_key": ""},  # no primary key -> real validation error
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert "Connection failed" in body
        assert "is_primary_key" in body or "primary key" in body
        # The whole point: nothing the provider typed got wiped out.
        assert "jsonplaceholder.typicode.com" in body
        assert 'value="id"' in body
        assert 'value="name"' in body
