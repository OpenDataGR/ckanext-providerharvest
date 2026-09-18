"""Self-service web UI for provider sources -- the Phase 2 piece that was
previously only reachable via the raw Action API (see README.md). Thin:
every route just builds a payload from the submitted form and hands it to
this extension's own actions (provider_source_create/_test_connection/
_activate/_list_mine/_list_pending), which already carry all the real
validation, org-scoping, and security logic -- this module has none of
its own.

Field-mapping rows use plain, JS-free repeatable form fields
(``row_rules-<n>-<key>``, a fixed number of pre-rendered slots) rather
than a dynamic JS editor -- fewer moving parts for an MVP, and DESIGN.md's
"mapping_editor" template can grow real add/remove JS later without
changing the submitted field shape.
"""

from __future__ import annotations

import flask

from ckan.plugins import toolkit

providerharvest = flask.Blueprint(
    "providerharvest", __name__, url_prefix="/provider-harvest"
)

#: Pre-rendered field-mapping rows in the form; empty ones are dropped
#: server-side. Plenty for a first cut -- see module docstring.
ROW_RULE_SLOTS = range(10)
FIELD_TYPES = ["text", "numeric", "integer", "boolean", "timestamp"]
TRANSFORMS = ["", "strip", "lower"]
FREQUENCIES = ["MANUAL", "DAILY", "WEEKLY", "MONTHLY"]


def _context() -> dict:
    return {"user": toolkit.g.user}


def _orgs_for_current_user() -> list[dict]:
    if not toolkit.g.user:
        return []
    try:
        return toolkit.get_action("organization_list_for_user")(
            dict(_context()), {"id": toolkit.g.user, "permission": "update_dataset"}
        )
    except toolkit.NotAuthorized:
        return []


def _row_rules_from_form(form) -> list[dict]:
    rules = []
    i = 0
    while True:
        ckan_field = form.get("row_rules-%d-ckan_field" % i)
        if ckan_field is None:
            break  # no more slots in the submitted form
        ckan_field = ckan_field.strip()
        source_path = form.get("row_rules-%d-source_path" % i, "").strip()
        if ckan_field and source_path:
            rules.append({
                "ckan_field": ckan_field,
                "source_path": source_path,
                "field_type": form.get("row_rules-%d-field_type" % i) or "text",
                "transform": form.get("row_rules-%d-transform" % i) or None,
                "is_primary_key": form.get("row_rules-%d-is_primary_key" % i) == "on",
                "required": form.get("row_rules-%d-required" % i) == "on",
            })
        i += 1
    return rules


def _auth_payload(form) -> dict:
    return {
        "credential_fields": {"api_key": form.get("api_key", "").strip()},
        "auth_opts": {
            "location": form.get("api_key_location") or "header",
            "name": form.get("api_key_name", "").strip() or "X-API-Key",
        },
        "pagination": {
            "style": form.get("pagination_style") or "page_number",
            "items_path": form.get("items_path", "").strip() or "results",
            "page_param": form.get("page_param", "").strip() or "page",
        },
    }


def _form_template_vars(data, errors=None) -> dict:
    return {
        "orgs": _orgs_for_current_user(),
        "data": data,
        "errors": errors or {},
        "field_types": FIELD_TYPES,
        "transforms": TRANSFORMS,
        "frequencies": FREQUENCIES,
        "row_rule_slots": ROW_RULE_SLOTS,
    }


def source_list():
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")
    try:
        sources = toolkit.get_action("provider_source_list_mine")(_context(), {})
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    return toolkit.render(
        "providerharvest/source_list.html", extra_vars={"sources": sources}
    )


def new_source():
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")

    if flask.request.method != "POST":
        return toolkit.render(
            "providerharvest/source_form.html", extra_vars=_form_template_vars({})
        )

    form = flask.request.form
    payload = {
        "name": form.get("name", "").strip(),
        "title": form.get("title", "").strip(),
        "owner_org": form.get("owner_org", "").strip(),
        "endpoint_url": form.get("endpoint_url", "").strip(),
        "transport_type": "http",
        "auth_type": "api_key",
        "frequency": form.get("frequency") or "DAILY",
        "dataset_defaults": {
            "title_translated": {"en": form.get("dataset_title", "").strip()},
            "notes_translated": {"en": form.get("dataset_notes", "").strip()},
        },
        "notification_email": form.get("notification_email", "").strip(),
        "row_rules": _row_rules_from_form(form),
        **_auth_payload(form),
    }
    try:
        result = toolkit.get_action("provider_source_create")(_context(), payload)
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except toolkit.ValidationError as exc:
        toolkit.h.flash_error(toolkit._("Please fix the errors below."))
        return toolkit.render(
            "providerharvest/source_form.html",
            extra_vars=_form_template_vars(form, exc.error_dict),
        )

    toolkit.h.flash_success(
        toolkit._('Source registered (status: "%s") and awaiting admin approval.')
        % result["status"]
    )
    return toolkit.redirect_to("providerharvest.source_list")


def test_connection():
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")

    form = flask.request.form
    payload = {
        "owner_org": form.get("owner_org", "").strip(),
        "endpoint_url": form.get("endpoint_url", "").strip(),
        "transport_type": "http",
        "row_rules": _row_rules_from_form(form),
        **_auth_payload(form),
    }
    result = None
    error = None
    try:
        result = toolkit.get_action("provider_source_test_connection")(_context(), payload)
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except Exception as exc:  # noqa: BLE001 -- shown to the provider verbatim, this IS the diagnostic
        error = str(exc)

    template_vars = _form_template_vars(form)
    template_vars.update({"result": result, "error": error})
    return toolkit.render(
        "providerharvest/test_connection_result.html", extra_vars=template_vars
    )


def admin_pending():
    try:
        pending = toolkit.get_action("provider_source_list_pending")(_context(), {})
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    return toolkit.render(
        "providerharvest/admin_pending.html", extra_vars={"pending": pending}
    )


def admin_activate(harvest_source_id):
    try:
        toolkit.get_action("provider_source_activate")(
            _context(), {"harvest_source_id": harvest_source_id}
        )
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except toolkit.ObjectNotFound:
        return toolkit.abort(404)
    toolkit.h.flash_success(toolkit._("Source activated."))
    return toolkit.redirect_to("providerharvest.admin_pending")


providerharvest.add_url_rule("/sources", view_func=source_list, methods=["GET"])
providerharvest.add_url_rule("/sources/new", view_func=new_source, methods=["GET", "POST"])
providerharvest.add_url_rule(
    "/sources/test-connection", view_func=test_connection, methods=["POST"]
)
providerharvest.add_url_rule("/admin/pending", view_func=admin_pending, methods=["GET"])
providerharvest.add_url_rule(
    "/admin/sources/<harvest_source_id>/activate",
    view_func=admin_activate,
    methods=["POST"],
)
