"""Self-service web UI for provider sources -- the Phase 2 piece that was
previously only reachable via the raw Action API (see README.md). Thin:
every route just builds a payload from the submitted form and hands it to
this extension's own actions, which already carry all the real
validation, org-scoping, and security logic -- this module has none of
its own.

Field-mapping rows use a small vanilla-JS add/remove editor
(source_form.html's ``<template>`` + inline ``<script>``, no framework/
build step) over the same ``row_rules-<n>-<key>`` field shape the
original JS-free MVP used -- a handful of rows are still pre-rendered so
the form still works with JS disabled, just without add/remove.
Removing a row client-side leaves a gap in the index sequence, so
``_row_rules_from_form`` below scans every present index rather than
stopping at the first missing one (a fixed-slots form never needed to).

The approval workflow has two admin views: ``admin_pending`` (the
original narrow queue -- Activate/Reject a pending registration) and
``admin_all_sources`` (every source regardless of status, with Pause/
Resume for active/paused ones). Pause/resume are also self-service, from
a provider's own ``source_list`` -- reachable by the owning org's admins,
not just data.gov.gr sysadmins, and both routes are shared between the
two callers (``came_from`` in the POST body decides where to redirect
back to, see ``_source_pause_or_resume``).
"""

from __future__ import annotations

import json
import re

import flask

from ckan.plugins import toolkit

providerharvest = flask.Blueprint(
    "providerharvest", __name__, url_prefix="/provider-harvest"
)

#: Pre-rendered field-mapping rows in the form before any JS add/remove;
#: covers the common case without scrolling, more can be added client-side.
ROW_RULE_SLOTS = range(3)
_ROW_RULE_INDEX_RE = re.compile(r"^row_rules-(\d+)-ckan_field$")
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
    # Scans every row_rules-<n>-ckan_field key present rather than
    # stopping at the first missing index -- a JS "remove row" can leave
    # a gap (e.g. rows 0 and 2 present, 1 removed), see module docstring.
    indices = sorted({
        int(match.group(1))
        for key in form.keys()
        for match in [_ROW_RULE_INDEX_RE.match(key)]
        if match
    })
    rules = []
    for i in indices:
        ckan_field = form.get("row_rules-%d-ckan_field" % i, "").strip()
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
    return rules


def _http_auth_payload(form) -> dict:
    """Dispatches on the "Authentication" sub-selector inside the HTTP
    API settings section -- api_key (the original, still the default),
    basic_auth, oauth2_client_credentials, or mtls, matching the
    auth_strategies package's own AUTH_STRATEGIES registry. Also carries
    pagination, since that's HTTP-specific too and lives in the same
    section of the form.
    """
    auth_type = form.get("auth_type") or "api_key"
    if auth_type == "basic_auth":
        credential_fields = {
            "username": form.get("basic_username", "").strip(),
            "password": form.get("basic_password", "").strip(),
        }
        auth_opts = {}
    elif auth_type == "oauth2_client_credentials":
        credential_fields = {
            "client_id": form.get("oauth2_client_id", "").strip(),
            "client_secret": form.get("oauth2_client_secret", "").strip(),
        }
        auth_opts = {
            "token_url": form.get("oauth2_token_url", "").strip(),
            "scope": form.get("oauth2_scope", "").strip() or None,
        }
    elif auth_type == "mtls":
        credential_fields = {
            "client_cert_pem": form.get("mtls_client_cert", "").strip(),
            "client_key_pem": form.get("mtls_client_key", "").strip(),
        }
        auth_opts = {}
    else:
        auth_type = "api_key"
        credential_fields = {"api_key": form.get("api_key", "").strip()}
        auth_opts = {
            "location": form.get("api_key_location") or "header",
            "name": form.get("api_key_name", "").strip() or "X-API-Key",
        }

    return {
        "auth_type": auth_type,
        "credential_fields": credential_fields,
        "auth_opts": auth_opts,
        "pagination": {
            "style": form.get("pagination_style") or "page_number",
            "items_path": form.get("items_path", "").strip() or "results",
            "page_param": form.get("page_param", "").strip() or "page",
        },
    }


def _ssh_payload(form) -> dict:
    """Everything specific to transport_type=sftp|scp -- both share this
    one fieldset in the form (a "SSH subsystem" choice picks which),
    since SFTPTransport/ScpTransport share the exact same connection/
    host-key/auth code: credentials (password or private key --
    whichever was filled in, matching SecretBundle's own either/or
    shape), and the host-key fingerprint the provider must have already
    fetched/confirmed via fetch_host_key (see that view).
    """
    credential_fields = {"username": form.get("sftp_username", "").strip()}
    private_key_pem = form.get("sftp_private_key", "").strip()
    if private_key_pem:
        credential_fields["private_key_pem"] = private_key_pem
        passphrase = form.get("sftp_private_key_passphrase", "").strip()
        if passphrase:
            credential_fields["private_key_passphrase"] = passphrase
    else:
        credential_fields["password"] = form.get("sftp_password", "").strip()

    return {
        "auth_type": "ssh_credentials",  # inert for sftp/scp -- see base_generic._build_transport
        "delivery_mode": "bulk_file",
        "credential_fields": credential_fields,
        "port": form.get("sftp_port", "").strip(),
        "remote_path": form.get("sftp_remote_path", "").strip() or "/",
        "glob_pattern": form.get("sftp_glob_pattern", "").strip() or "*",
        "host_key_fingerprint": form.get("host_key_fingerprint", "").strip(),
    }


def _ftp_payload(form) -> dict:
    """Everything specific to transport_type=ftp: credentials (username +
    password only -- FTP has no private-key auth concept), and the
    use_tls/plain_ftp_acknowledged pair _require_ftp_fields cross-
    validates (FTPS is the default; plain FTP needs the checkbox)."""
    return {
        "auth_type": "ftp_credentials",  # inert for ftp -- see base_generic._build_transport
        "delivery_mode": "bulk_file",
        "credential_fields": {
            "username": form.get("ftp_username", "").strip(),
            "password": form.get("ftp_password", "").strip(),
        },
        "port": form.get("ftp_port", "").strip(),
        "remote_path": form.get("ftp_remote_path", "").strip() or "/",
        "glob_pattern": form.get("ftp_glob_pattern", "").strip() or "*",
        "use_tls": "true" if form.get("ftp_use_tls") == "on" else "false",
        "plain_ftp_acknowledged": "true" if form.get("ftp_plain_ftp_acknowledged") == "on" else "false",
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


def _transport_type(source: dict) -> str:
    try:
        return json.loads(source.get("config") or "{}").get("transport_type", "?")
    except ValueError:
        return "?"


def _admin_org_ids_for_current_user() -> set:
    """Orgs the current user can pause/resume sources in -- provider_
    source_pause/_resume require org-admin, one level above the editor
    role provider_source_list_mine itself only needs, so knowing this
    lets the template skip rendering a button that would just 403."""
    if not toolkit.g.user:
        return set()
    try:
        orgs = toolkit.get_action("organization_list_for_user")(
            dict(_context()), {"id": toolkit.g.user, "permission": "admin"}
        )
    except toolkit.NotAuthorized:
        return set()
    return {org["id"] for org in orgs}


def source_list():
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")
    try:
        sources = toolkit.get_action("provider_source_list_mine")(_context(), {})
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    return toolkit.render(
        "providerharvest/source_list.html",
        extra_vars={
            "sources": [(s, _transport_type(s)) for s in sources],
            "admin_org_ids": _admin_org_ids_for_current_user(),
        },
    )


def _resolve_transport_type(form) -> str:
    """The form's top-level selector offers "http"/"ssh"/"ftp" -- "ssh"
    isn't a real transport_type the backend understands, it's a UI-only
    grouping so SFTP and SCP (which share every field except which SSH
    subsystem to use) don't need two near-duplicate fieldsets. A second
    selector inside the SSH section (ssh_subsystem) picks the real
    sftp/scp value."""
    choice = form.get("transport_type") or "http"
    if choice == "ssh":
        return form.get("ssh_subsystem") or "sftp"
    return choice


def _payload_from_form(form) -> dict:
    transport_type = _resolve_transport_type(form)
    payload = {
        "name": form.get("name", "").strip(),
        "title": form.get("title", "").strip(),
        "owner_org": form.get("owner_org", "").strip(),
        "endpoint_url": form.get("endpoint_url", "").strip(),
        "transport_type": transport_type,
        "frequency": form.get("frequency") or "DAILY",
        "dataset_defaults": {
            "title_translated": {"en": form.get("dataset_title", "").strip()},
            "notes_translated": {"en": form.get("dataset_notes", "").strip()},
        },
        "notification_email": form.get("notification_email", "").strip(),
    }
    if transport_type in ("sftp", "scp"):
        payload.update(_ssh_payload(form))
    elif transport_type == "ftp":
        payload.update(_ftp_payload(form))
    else:
        payload["row_rules"] = _row_rules_from_form(form)
        payload.update(_http_auth_payload(form))
    return payload


def new_source():
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")

    if flask.request.method != "POST":
        return toolkit.render(
            "providerharvest/source_form.html", extra_vars=_form_template_vars({})
        )

    form = flask.request.form
    payload = _payload_from_form(form)
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
    """Renders the same source_form.html the provider was already
    filling in (all fields preserved), with the test result/error shown
    inline -- NOT a separate result page with a "back to form" link,
    which used to throw away everything they'd typed the moment a test
    failed (a real usability bug found via actually using this UI, not
    just its automated tests). Same pattern fetch_host_key() already
    uses for the same reason.
    """
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")

    form = flask.request.form
    payload = _payload_from_form(form)
    result = None
    error = None
    try:
        result = toolkit.get_action("provider_source_test_connection")(_context(), payload)
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except Exception as exc:  # noqa: BLE001 -- shown to the provider verbatim, this IS the diagnostic
        error = str(exc)

    template_vars = _form_template_vars(form.to_dict())
    template_vars.update({"test_result": result, "test_error": error})
    return toolkit.render(
        "providerharvest/source_form.html", extra_vars=template_vars
    )


def fetch_host_key():
    """The trust-on-first-use step SFTP registration requires: reads the
    server's current SSH host-key fingerprint and re-renders the same
    form (all other fields preserved) with it filled into
    host_key_fingerprint, for the provider to visually confirm against
    what the server operator gave them out-of-band before it gets pinned
    by the actual "Register source" submit -- see
    provider_source_fetch_host_key's own docstring for why this can't be
    skipped or defaulted."""
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")

    form = flask.request.form
    payload = {
        "owner_org": form.get("owner_org", "").strip(),
        "endpoint_url": form.get("endpoint_url", "").strip(),
        "port": form.get("sftp_port", "").strip(),
    }
    try:
        result = toolkit.get_action("provider_source_fetch_host_key")(_context(), payload)
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except Exception as exc:  # noqa: BLE001 -- shown to the provider verbatim, this IS the diagnostic
        toolkit.h.flash_error(toolkit._("Could not fetch the host key: %s") % exc)
        return toolkit.render(
            "providerharvest/source_form.html", extra_vars=_form_template_vars(form)
        )

    toolkit.h.flash_success(
        toolkit._(
            'Host key fingerprint for %(host)s: %(fingerprint)s -- confirm this matches '
            'what the provider gave you out-of-band, then register the source.'
        ) % result
    )
    data = form.to_dict()
    data["host_key_fingerprint"] = result["fingerprint"]
    return toolkit.render(
        "providerharvest/source_form.html", extra_vars=_form_template_vars(data)
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


def admin_reject(harvest_source_id):
    reason = flask.request.form.get("reason", "").strip()
    try:
        toolkit.get_action("provider_source_reject")(
            _context(), {"harvest_source_id": harvest_source_id, "reason": reason}
        )
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except toolkit.ObjectNotFound:
        return toolkit.abort(404)
    except toolkit.ValidationError as exc:
        toolkit.h.flash_error(str(exc))
        return toolkit.redirect_to("providerharvest.admin_pending")
    toolkit.h.flash_success(toolkit._("Source rejected."))
    return toolkit.redirect_to("providerharvest.admin_pending")


def admin_all_sources():
    """The "richer" admin dashboard: every source regardless of status,
    with Activate/Reject/Pause/Resume all reachable from one page --
    admin_pending stays focused on just the pending queue."""
    try:
        sources = toolkit.get_action("provider_source_list_all")(_context(), {})
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    return toolkit.render(
        "providerharvest/admin_all_sources.html", extra_vars={"sources": sources}
    )


def source_pause(harvest_source_id):
    return _source_pause_or_resume("provider_source_pause", harvest_source_id, "paused")


def source_resume(harvest_source_id):
    return _source_pause_or_resume("provider_source_resume", harvest_source_id, "resumed")


def _source_pause_or_resume(action_name: str, harvest_source_id: str, past_tense: str):
    if not toolkit.g.user:
        return toolkit.redirect_to("user.login")
    # Self-service (org admins) and the sysadmin admin dashboard both
    # link to these same two routes -- redirect back to wherever the
    # request actually came from rather than hardcoding one destination.
    referrer = flask.request.form.get("came_from") or toolkit.h.url_for(
        "providerharvest.source_list"
    )
    try:
        toolkit.get_action(action_name)(_context(), {"harvest_source_id": harvest_source_id})
    except toolkit.NotAuthorized:
        return toolkit.abort(403)
    except toolkit.ObjectNotFound:
        return toolkit.abort(404)
    except toolkit.ValidationError as exc:
        toolkit.h.flash_error(str(exc))
        return toolkit.redirect_to(referrer)
    toolkit.h.flash_success(toolkit._("Source %s.") % past_tense)
    return toolkit.redirect_to(referrer)


providerharvest.add_url_rule("/sources", view_func=source_list, methods=["GET"])
providerharvest.add_url_rule("/sources/new", view_func=new_source, methods=["GET", "POST"])
providerharvest.add_url_rule(
    "/sources/test-connection", view_func=test_connection, methods=["POST"]
)
providerharvest.add_url_rule(
    "/sources/fetch-host-key", view_func=fetch_host_key, methods=["POST"]
)
providerharvest.add_url_rule(
    "/sources/<harvest_source_id>/pause", view_func=source_pause, methods=["POST"]
)
providerharvest.add_url_rule(
    "/sources/<harvest_source_id>/resume", view_func=source_resume, methods=["POST"]
)
providerharvest.add_url_rule("/admin/pending", view_func=admin_pending, methods=["GET"])
providerharvest.add_url_rule(
    "/admin/sources/<harvest_source_id>/activate",
    view_func=admin_activate,
    methods=["POST"],
)
providerharvest.add_url_rule(
    "/admin/sources/<harvest_source_id>/reject",
    view_func=admin_reject,
    methods=["POST"],
)
providerharvest.add_url_rule("/admin/sources", view_func=admin_all_sources, methods=["GET"])
