"""Table definitions for this extension, following the same pattern
ckanext-harvest itself uses (plain SQLAlchemy Core tables bound to CKAN's
metadata, created via an idempotent ``init_tables()`` called from the
plugin's setup).
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from ckan.model.meta import Session, metadata
from sqlalchemy.orm import registry

mapper_registry = registry()

provider_source_extension_table = sa.Table(
    "providerharvest_provider_source",
    metadata,
    sa.Column("id", sa.UnicodeText, primary_key=True),
    # 1:1 with ckanext-harvest's HarvestSource.id -- not a formal FK across
    # extensions, matching ckanext-harvest's own convention for such links.
    sa.Column("harvest_source_id", sa.UnicodeText, nullable=False, unique=True, index=True),
    sa.Column("owner_org", sa.UnicodeText, nullable=False, index=True),
    sa.Column("delivery_mode", sa.UnicodeText, nullable=False, default="api_records"),
    sa.Column("status", sa.UnicodeText, nullable=False, default="pending"),  # pending|active|paused
    sa.Column("ckan_package_id", sa.UnicodeText, nullable=True),
    sa.Column("ckan_resource_id", sa.UnicodeText, nullable=True),
    sa.Column("datastore_initialized", sa.Boolean, nullable=False, default=False),
    sa.Column("notification_email", sa.UnicodeText, nullable=True),
    sa.Column("notification_webhook_url", sa.UnicodeText, nullable=True),
    sa.Column("consecutive_failure_count", sa.Integer, nullable=False, default=0),
    sa.Column("last_notified_at", sa.DateTime, nullable=True),
    sa.Column("host_key_fingerprint", sa.UnicodeText, nullable=True),  # SFTP/SCP only
    sa.Column("plain_ftp_acknowledged", sa.Boolean, nullable=False, default=False),
    sa.Column("created", sa.DateTime, default=datetime.datetime.utcnow),
)

field_mapping_profile_table = sa.Table(
    "providerharvest_field_mapping_profile",
    metadata,
    sa.Column("id", sa.UnicodeText, primary_key=True),
    sa.Column("harvest_source_id", sa.UnicodeText, nullable=False, index=True),
    sa.Column("row_rules_json", sa.UnicodeText, nullable=False),  # serialized FieldMappingRule list
    sa.Column("dataset_defaults_json", sa.UnicodeText, nullable=False, default="{}"),
    sa.Column("resource_defaults_json", sa.UnicodeText, nullable=False, default="{}"),
    sa.Column("created", sa.DateTime, default=datetime.datetime.utcnow),
)

secret_table = sa.Table(
    "providerharvest_secret",
    metadata,
    sa.Column("id", sa.UnicodeText, primary_key=True),
    sa.Column("harvest_source_id", sa.UnicodeText, nullable=False, index=True),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("data_key_wrapped", sa.LargeBinary, nullable=False),
    sa.Column("algorithm", sa.UnicodeText, nullable=False),
    sa.Column("version", sa.Integer, nullable=False, default=1),
    sa.Column("created", sa.DateTime, default=datetime.datetime.utcnow),
    sa.Column("rotated_at", sa.DateTime, nullable=True),
)

outbound_request_log_table = sa.Table(
    "providerharvest_outbound_request_log",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("harvest_source_id", sa.UnicodeText, nullable=False, index=True),
    sa.Column("harvest_job_id", sa.UnicodeText, nullable=True, index=True),
    sa.Column("host", sa.UnicodeText, nullable=False),
    sa.Column("path", sa.UnicodeText, nullable=False),
    sa.Column("method", sa.UnicodeText, nullable=False),
    sa.Column("status", sa.Integer, nullable=True),
    sa.Column("duration_ms", sa.Float, nullable=False),
    sa.Column("auth_type_used", sa.UnicodeText, nullable=False),
    sa.Column("error", sa.UnicodeText, nullable=True),
    sa.Column("timestamp", sa.DateTime, default=datetime.datetime.utcnow, index=True),
)


def init_tables() -> None:
    """Idempotently create this extension's tables. Called from
    ``plugin.py``'s configure/update_config, same pattern ckanext-harvest
    itself uses for its own model.

    ``metadata.bind`` is never set on CKAN 2.11's SQLAlchemy 1.4+ engine
    setup (``MetaData.bind`` is legacy/deprecated there), so use the
    engine actually bound to CKAN's own scoped Session instead -- the
    same source ckanext-harvest's own ``model.setup()`` draws from, and
    reliably already configured by the time a plugin's ``configure()``
    runs during CKAN's environment/plugin load.
    """
    metadata.create_all(bind=Session.get_bind(), checkfirst=True)
