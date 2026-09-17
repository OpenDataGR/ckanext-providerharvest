"""Org-scoped permissions for provider self-service.

Stock ``ckanext-harvest`` gates its harvest-source admin UI behind
sysadmin. That's wrong for this extension's goal (providers manage their
own sources without needing sysadmin rights) -- these auth functions
replace that gate with an org-membership check: any editor/admin of the
owning organization may manage that organization's provider sources.
"""

from __future__ import annotations

from ckan.plugins import toolkit


def _has_org_role(context: dict, owner_org: str, minimum_role: str) -> bool:
    """True if the current user has at least ``minimum_role`` on ``owner_org``.

    Delegates to CKAN's own authz helper rather than re-implementing role
    comparison -- ``editor`` and ``admin`` are the two roles relevant here.
    """
    user_name = context.get("user")
    if not user_name:
        return False
    if toolkit.check_ckan_version(min_version="2.9"):
        from ckan import authz
        if minimum_role == "admin":
            return authz.has_user_permission_for_group_or_org(owner_org, user_name, "admin")
        return authz.has_user_permission_for_group_or_org(owner_org, user_name, "update_dataset")
    return False


def provider_source_create(context: dict, data_dict: dict) -> dict:
    owner_org = data_dict.get("owner_org")
    if not owner_org:
        return {"success": False, "msg": "owner_org is required"}
    return {"success": _has_org_role(context, owner_org, "editor")}


def provider_source_test_connection(context: dict, data_dict: dict) -> dict:
    # Same requirement as create: this runs pre-save, before a
    # ProviderSourceExtension row necessarily exists yet.
    return provider_source_create(context, data_dict)


def provider_source_show(context: dict, data_dict: dict) -> dict:
    owner_org = data_dict.get("owner_org")
    if not owner_org:
        return {"success": False, "msg": "owner_org is required"}
    return {"success": _has_org_role(context, owner_org, "editor")}


def provider_source_list_mine(context: dict, data_dict: dict) -> dict:
    # Any logged-in user may call this; results are filtered server-side
    # to organizations they belong to (see logic.action.provider_source_list_mine).
    return {"success": bool(context.get("user"))}


def provider_source_update(context: dict, data_dict: dict) -> dict:
    owner_org = data_dict.get("owner_org")
    if not owner_org:
        return {"success": False, "msg": "owner_org is required"}
    return {"success": _has_org_role(context, owner_org, "editor")}


def provider_source_delete(context: dict, data_dict: dict) -> dict:
    # Deliberately stricter than create/update: turning a source off/on
    # is restricted to org admins, per the plan's role-granularity default.
    owner_org = data_dict.get("owner_org")
    if not owner_org:
        return {"success": False, "msg": "owner_org is required"}
    return {"success": _has_org_role(context, owner_org, "admin")}


def provider_source_activate(context: dict, data_dict: dict) -> dict:
    """Flipping pending -> active is a data.gov.gr admin action (the
    approval gate), never something the provider grants themselves. This
    unconditionally denies non-sysadmins; CKAN's own ``check_access``
    already short-circuits to allow sysadmins before this function is
    ever called, which is what makes it reachable at all."""
    return {"success": False}
