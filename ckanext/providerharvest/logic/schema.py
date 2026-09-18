"""Validation schemas for the onboarding actions, in CKAN's own
navl-dictization style (a dict of field name -> list of validators).

Fields whose value is a list-of-dicts or a plain dict (``credential_fields``,
``row_rules``, ``pagination``, ``auth_opts``, ``dataset_defaults``,
``resource_defaults``) are deliberately NOT in this schema:
``toolkit.navl_validate`` (``ckan.lib.navl.dictization_functions.flatten_dict``)
unconditionally flattens any such value into ``('field', 0, 'subkey')``-style
tuple keys before validating, regardless of what the schema declares for
that key -- there is no way to make navl treat one as a single opaque
value. The original top-level key then looks Missing (its content lands
in ``__junk`` instead), so every one of these was actually unvalidatable
through this schema despite being listed in it. See
``logic/action.py``'s ``_pop_nested_fields`` for where they're validated
instead.
"""

from __future__ import annotations

from ckan.plugins import toolkit

not_empty = toolkit.get_validator("not_empty")
ignore_missing = toolkit.get_validator("ignore_missing")
unicode_safe = toolkit.get_validator("unicode_safe")

#: Keys validated by hand in logic/action.py instead of through this
#: schema -- see the module docstring above for why.
NESTED_FIELDS = (
    "credential_fields", "row_rules", "pagination", "auth_opts",
    "dataset_defaults", "resource_defaults",
)
#: Which of NESTED_FIELDS must be non-empty.
REQUIRED_NESTED_FIELDS = ("credential_fields", "row_rules")


def provider_source_create_schema() -> dict:
    return {
        "name": [not_empty, unicode_safe],
        "title": [ignore_missing, unicode_safe],
        "owner_org": [not_empty, unicode_safe],
        "endpoint_url": [not_empty, unicode_safe],
        "transport_type": [not_empty, unicode_safe],
        "auth_type": [not_empty, unicode_safe],
        "frequency": [ignore_missing, unicode_safe],
        "delivery_mode": [ignore_missing, unicode_safe],
        "notification_email": [ignore_missing, unicode_safe],
        "notification_webhook_url": [ignore_missing, unicode_safe],
        # SFTP-only (transport_type="sftp"); left optional at the schema
        # level and cross-validated by hand in logic/action.py -- navl has
        # no "required only if some other field equals X" validator, and
        # these fields are meaningless (and correctly absent) for
        # transport_type="http" sources.
        "port": [ignore_missing, unicode_safe],
        "remote_path": [ignore_missing, unicode_safe],
        "glob_pattern": [ignore_missing, unicode_safe],
        "host_key_fingerprint": [ignore_missing, unicode_safe],
    }
