"""These tests need a real CKAN + ckanext-harvest environment (the Docker
replica under docker/), unlike the rest of ckanext/providerharvest/tests,
which are intentionally CKAN-independent so `pytest
ckanext/providerharvest/tests` also works in a plain venv (see README.md).
Skip collecting this directory there instead of failing at import time.
"""

import pytest

pytest.importorskip("ckan", reason="integration tests need a real CKAN environment")
pytest.importorskip("ckanext.harvest", reason="integration tests need ckanext-harvest installed")


@pytest.fixture(autouse=True)
def _recreate_providerharvest_tables(clean_db):
    # ckan.tests' clean_db fixture drops every table and recreates only
    # CKAN core's + any Alembic-migrated extensions' (e.g. ckanext-harvest's
    # own). This extension's tables are never in that migration chain --
    # they're created exactly once, at process startup, via
    # IConfigurable.configure() -> model.meta.init_tables() -- so without
    # this they simply don't exist for any test after the first clean_db
    # reset. Depending on clean_db as a fixture parameter (not just via
    # usefixtures) guarantees this runs after it, not before.
    from ckanext.providerharvest.model.meta import init_tables
    init_tables()
