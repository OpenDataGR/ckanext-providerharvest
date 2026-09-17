"""Persists ``OutboundRequestEvent``s from any transport as audit rows.

Deliberately never stores headers or bodies -- only host/path/status/timing
-- since that's where provider credentials would otherwise leak into logs.
"""

from __future__ import annotations

from ckan.model.meta import Session

from ckanext.providerharvest.audit import OutboundRequestEvent
from ckanext.providerharvest.model.meta import mapper_registry, outbound_request_log_table


class OutboundRequestLog:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


mapper_registry.map_imperatively(OutboundRequestLog, outbound_request_log_table)


def record_event(event: OutboundRequestEvent) -> None:
    row = OutboundRequestLog(
        harvest_source_id=event.harvest_source_id,
        harvest_job_id=event.harvest_job_id,
        host=event.host,
        path=event.path,
        method=event.method,
        status=event.status,
        duration_ms=event.duration_ms,
        auth_type_used=event.auth_type_used,
        error=event.error,
        timestamp=event.timestamp,
    )
    Session.add(row)
    Session.commit()
