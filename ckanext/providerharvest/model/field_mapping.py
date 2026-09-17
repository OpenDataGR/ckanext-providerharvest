from __future__ import annotations

import dataclasses
import json
import uuid

from ckan.model.meta import Session

from ckanext.providerharvest.mapping import FieldMappingProfile, FieldMappingRule
from ckanext.providerharvest.model.meta import field_mapping_profile_table, mapper_registry


class FieldMappingProfileRow:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id") or str(uuid.uuid4())
        for key, value in kwargs.items():
            setattr(self, key, value)


mapper_registry.map_imperatively(FieldMappingProfileRow, field_mapping_profile_table)


def save(harvest_source_id: str, profile: FieldMappingProfile) -> str:
    row = FieldMappingProfileRow(
        harvest_source_id=harvest_source_id,
        row_rules_json=json.dumps([dataclasses.asdict(r) for r in profile.row_rules]),
        dataset_defaults_json=json.dumps(profile.dataset_defaults),
        resource_defaults_json=json.dumps(profile.resource_defaults),
    )
    Session.add(row)
    Session.commit()
    return row.id


def load_latest(harvest_source_id: str) -> FieldMappingProfile:
    row = (
        Session.query(FieldMappingProfileRow)
        .filter_by(harvest_source_id=harvest_source_id)
        .order_by(FieldMappingProfileRow.created.desc())
        .first()
    )
    if row is None:
        raise LookupError("No field mapping profile for harvest_source_id=%r" % harvest_source_id)

    rule_dicts = json.loads(row.row_rules_json)
    rules = [
        FieldMappingRule(
            ckan_field=r["ckan_field"],
            source_path=r["source_path"],
            field_type=r.get("field_type", "text"),
            transform=r.get("transform"),
            is_primary_key=r.get("is_primary_key", False),
            required=r.get("required", False),
        )
        for r in rule_dicts
    ]
    return FieldMappingProfile(
        row_rules=rules,
        dataset_defaults=json.loads(row.dataset_defaults_json),
        resource_defaults=json.loads(row.resource_defaults_json),
    )
