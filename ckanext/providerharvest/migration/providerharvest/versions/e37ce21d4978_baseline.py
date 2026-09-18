"""baseline: create providerharvest tables

Revision ID: e37ce21d4978
Revises:
Create Date: 2026-09-18 00:00:00.000000

This is a *baseline* revision, not a from-scratch one: this extension's
tables previously existed already, created by a hand-rolled
``metadata.create_all(checkfirst=True)`` call (``model/meta.py``'s
``init_tables()``, formerly invoked from ``IConfigurable.configure()`` --
now removed in favour of this real Alembic migration, matching
ckanext-harvest's own convention). Every ``create_table``/``create_index``
call below is guarded the same way ckanext-harvest's own baseline
revision (``3b4894672727_create_harvest_tables.py``) is: a no-op if the
table/index already exists (an already-deployed replica upgrading from
the old mechanism), a real create on a genuinely fresh database. Either
way, Alembic records this revision as applied in this plugin's own
``providerharvest_alembic_version`` table, so it becomes the fixed point
every later real schema change (a normal ``op.add_column`` etc. in its
own revision) layers on top of via ``down_revision`` chaining.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e37ce21d4978"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    engine = op.get_bind()
    inspector = sa.inspect(engine)
    tables = inspector.get_table_names()

    if "providerharvest_provider_source" not in tables:
        op.create_table(
            "providerharvest_provider_source",
            sa.Column("id", sa.UnicodeText, primary_key=True),
            # 1:1 with ckanext-harvest's HarvestSource.id -- not a formal
            # FK across extensions, matching ckanext-harvest's own
            # convention for such links.
            sa.Column("harvest_source_id", sa.UnicodeText, nullable=False),
            sa.Column("owner_org", sa.UnicodeText, nullable=False),
            sa.Column("delivery_mode", sa.UnicodeText, nullable=False),
            sa.Column("status", sa.UnicodeText, nullable=False),  # pending|active|paused|rejected
            sa.Column("ckan_package_id", sa.UnicodeText, nullable=True),
            sa.Column("ckan_resource_id", sa.UnicodeText, nullable=True),
            sa.Column("datastore_initialized", sa.Boolean, nullable=False),
            sa.Column("notification_email", sa.UnicodeText, nullable=True),
            sa.Column("notification_webhook_url", sa.UnicodeText, nullable=True),
            sa.Column("consecutive_failure_count", sa.Integer, nullable=False),
            sa.Column("last_notified_at", sa.DateTime, nullable=True),
            sa.Column("host_key_fingerprint", sa.UnicodeText, nullable=True),  # SFTP/SCP only
            sa.Column("plain_ftp_acknowledged", sa.Boolean, nullable=False),
            # Set only when status="rejected" -- shown back to the
            # provider on their own source list so a denial isn't a
            # silent dead end.
            sa.Column("rejection_reason", sa.UnicodeText, nullable=True),
            sa.Column("created", sa.DateTime),
        )

    index_names = [
        index["name"] for index in inspector.get_indexes("providerharvest_provider_source")
    ] if "providerharvest_provider_source" in tables else []
    if "ix_providerharvest_provider_source_harvest_source_id" not in index_names:
        op.create_index(
            "ix_providerharvest_provider_source_harvest_source_id",
            "providerharvest_provider_source", ["harvest_source_id"], unique=True,
        )
    if "ix_providerharvest_provider_source_owner_org" not in index_names:
        op.create_index(
            "ix_providerharvest_provider_source_owner_org",
            "providerharvest_provider_source", ["owner_org"],
        )

    if "providerharvest_field_mapping_profile" not in tables:
        op.create_table(
            "providerharvest_field_mapping_profile",
            sa.Column("id", sa.UnicodeText, primary_key=True),
            sa.Column("harvest_source_id", sa.UnicodeText, nullable=False),
            sa.Column("row_rules_json", sa.UnicodeText, nullable=False),  # serialized FieldMappingRule list
            sa.Column("dataset_defaults_json", sa.UnicodeText, nullable=False),
            sa.Column("resource_defaults_json", sa.UnicodeText, nullable=False),
            sa.Column("created", sa.DateTime),
        )

    index_names = [
        index["name"] for index in inspector.get_indexes("providerharvest_field_mapping_profile")
    ] if "providerharvest_field_mapping_profile" in tables else []
    if "ix_providerharvest_field_mapping_profile_harvest_source_id" not in index_names:
        op.create_index(
            "ix_providerharvest_field_mapping_profile_harvest_source_id",
            "providerharvest_field_mapping_profile", ["harvest_source_id"],
        )

    if "providerharvest_secret" not in tables:
        op.create_table(
            "providerharvest_secret",
            sa.Column("id", sa.UnicodeText, primary_key=True),
            sa.Column("harvest_source_id", sa.UnicodeText, nullable=False),
            sa.Column("ciphertext", sa.LargeBinary, nullable=False),
            sa.Column("data_key_wrapped", sa.LargeBinary, nullable=False),
            sa.Column("algorithm", sa.UnicodeText, nullable=False),
            sa.Column("version", sa.Integer, nullable=False),
            sa.Column("created", sa.DateTime),
            sa.Column("rotated_at", sa.DateTime, nullable=True),
        )

    index_names = [
        index["name"] for index in inspector.get_indexes("providerharvest_secret")
    ] if "providerharvest_secret" in tables else []
    if "ix_providerharvest_secret_harvest_source_id" not in index_names:
        op.create_index(
            "ix_providerharvest_secret_harvest_source_id",
            "providerharvest_secret", ["harvest_source_id"],
        )

    if "providerharvest_outbound_request_log" not in tables:
        op.create_table(
            "providerharvest_outbound_request_log",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("harvest_source_id", sa.UnicodeText, nullable=False),
            sa.Column("harvest_job_id", sa.UnicodeText, nullable=True),
            sa.Column("host", sa.UnicodeText, nullable=False),
            sa.Column("path", sa.UnicodeText, nullable=False),
            sa.Column("method", sa.UnicodeText, nullable=False),
            sa.Column("status", sa.Integer, nullable=True),
            sa.Column("duration_ms", sa.Float, nullable=False),
            sa.Column("auth_type_used", sa.UnicodeText, nullable=False),
            sa.Column("error", sa.UnicodeText, nullable=True),
            sa.Column("timestamp", sa.DateTime),
        )

    index_names = [
        index["name"] for index in inspector.get_indexes("providerharvest_outbound_request_log")
    ] if "providerharvest_outbound_request_log" in tables else []
    if "ix_providerharvest_outbound_request_log_harvest_source_id" not in index_names:
        op.create_index(
            "ix_providerharvest_outbound_request_log_harvest_source_id",
            "providerharvest_outbound_request_log", ["harvest_source_id"],
        )
    if "ix_providerharvest_outbound_request_log_harvest_job_id" not in index_names:
        op.create_index(
            "ix_providerharvest_outbound_request_log_harvest_job_id",
            "providerharvest_outbound_request_log", ["harvest_job_id"],
        )
    if "ix_providerharvest_outbound_request_log_timestamp" not in index_names:
        op.create_index(
            "ix_providerharvest_outbound_request_log_timestamp",
            "providerharvest_outbound_request_log", ["timestamp"],
        )


def downgrade():
    op.drop_table("providerharvest_outbound_request_log")
    op.drop_table("providerharvest_secret")
    op.drop_table("providerharvest_field_mapping_profile")
    op.drop_table("providerharvest_provider_source")
