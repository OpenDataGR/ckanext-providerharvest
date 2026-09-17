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
def _ensure_providerharvest_tables():
    # Idempotent safety net (checkfirst=True in init_tables itself), not
    # actually load-bearing under normal circumstances: these tests
    # deliberately don't use ckan.tests' clean_db fixture (see the note
    # in test_e2e_harvest.py's class docstring) specifically because it
    # drops every table and only recreates CKAN core's + Alembic-migrated
    # extensions' -- both this extension's own tables AND
    # ckanext-datastore's internal _table_metadata view are casualties of
    # that (created once at process startup, never in that migration
    # chain), and there's no supported way to cheaply recreate the latter
    # from a test. Simpler to just not drop any of it in the first place.
    from ckanext.providerharvest.model.meta import init_tables
    init_tables()
