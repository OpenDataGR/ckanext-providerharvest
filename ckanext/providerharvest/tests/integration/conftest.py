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
    # actually load-bearing under normal circumstances -- this
    # extension's own tables are Alembic-migrated now
    # (migration/providerharvest/), applied automatically by `ckan db
    # init` on every container start, same as when this comment was
    # first written (that was true even of the old metadata.create_all()
    # mechanism this replaced). Still avoiding ckan.tests' clean_db
    # fixture here regardless (see the note in test_e2e_harvest.py's
    # class docstring): it drops every table, and ckanext-datastore's
    # internal _table_metadata view is a real casualty of that -- it's
    # created once via a raw-SQL step outside any Alembic chain, core's
    # or any plugin's, and there's no supported way to cheaply recreate
    # it from a test. Simpler to just not drop any of it in the first place.
    from ckanext.providerharvest.model.meta import init_tables
    init_tables()
