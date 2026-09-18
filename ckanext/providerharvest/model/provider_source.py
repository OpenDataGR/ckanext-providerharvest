from __future__ import annotations

import uuid

from ckan.model.meta import Session

from ckanext.providerharvest.model.meta import mapper_registry, provider_source_extension_table


class ProviderSourceExtension:
    """Per-source state that doesn't belong on ckanext-harvest's own
    HarvestSource: which CKAN package/resource this source's data lands
    in, provider-facing status/approval, notification contact, and
    SSH host-key pinning."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id") or str(uuid.uuid4())
        for key, value in kwargs.items():
            setattr(self, key, value)


mapper_registry.map_imperatively(ProviderSourceExtension, provider_source_extension_table)


def get_by_harvest_source_id(harvest_source_id: str) -> ProviderSourceExtension | None:
    return (
        Session.query(ProviderSourceExtension)
        .filter_by(harvest_source_id=harvest_source_id)
        .first()
    )


def list_by_status(status: str) -> list[ProviderSourceExtension]:
    """Across all orgs -- used by the sysadmin approval queue, which by
    definition needs to see pending sources regardless of which org they
    belong to (unlike provider_source_list_mine, which is deliberately
    scoped to the calling user's own orgs)."""
    return (
        Session.query(ProviderSourceExtension)
        .filter_by(status=status)
        .order_by(ProviderSourceExtension.created)
        .all()
    )


def create(**kwargs) -> ProviderSourceExtension:
    obj = ProviderSourceExtension(**kwargs)
    Session.add(obj)
    Session.commit()
    return obj


def record_failure(harvest_source_id: str) -> ProviderSourceExtension:
    """Bump the consecutive-failure counter; caller decides whether this
    crosses the notification threshold."""
    obj = get_by_harvest_source_id(harvest_source_id)
    if obj is None:
        raise LookupError("No ProviderSourceExtension for harvest_source_id=%r" % harvest_source_id)
    obj.consecutive_failure_count = (obj.consecutive_failure_count or 0) + 1
    Session.commit()
    return obj


def record_success(harvest_source_id: str) -> ProviderSourceExtension:
    obj = get_by_harvest_source_id(harvest_source_id)
    if obj is None:
        raise LookupError("No ProviderSourceExtension for harvest_source_id=%r" % harvest_source_id)
    obj.consecutive_failure_count = 0
    Session.commit()
    return obj
