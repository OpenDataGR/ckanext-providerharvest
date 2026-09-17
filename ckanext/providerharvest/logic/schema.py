"""Validation schemas for the onboarding actions, in CKAN's own
navl-dictization style (a dict of field name -> list of validators)."""

from __future__ import annotations

from ckan.plugins import toolkit

not_empty = toolkit.get_validator("not_empty")
ignore_missing = toolkit.get_validator("ignore_missing")
unicode_safe = toolkit.get_validator("unicode_safe")


def provider_source_create_schema() -> dict:
    return {
        "name": [not_empty, unicode_safe],
        "title": [ignore_missing, unicode_safe],
        "owner_org": [not_empty, unicode_safe],
        "endpoint_url": [not_empty, unicode_safe],
        "transport_type": [not_empty, unicode_safe],
        "auth_type": [not_empty, unicode_safe],
        "credential_fields": [not_empty],
        "row_rules": [not_empty],
        "frequency": [ignore_missing, unicode_safe],
        "pagination": [ignore_missing],
        "auth_opts": [ignore_missing],
        "delivery_mode": [ignore_missing, unicode_safe],
        "dataset_defaults": [ignore_missing],
        "resource_defaults": [ignore_missing],
        "notification_email": [ignore_missing, unicode_safe],
        "notification_webhook_url": [ignore_missing, unicode_safe],
    }
