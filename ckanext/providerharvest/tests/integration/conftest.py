"""These tests need a real CKAN + ckanext-harvest environment (the Docker
replica under docker/), unlike the rest of ckanext/providerharvest/tests,
which are intentionally CKAN-independent so `pytest
ckanext/providerharvest/tests` also works in a plain venv (see README.md).
Skip collecting this directory there instead of failing at import time.
"""

import pytest

pytest.importorskip("ckan", reason="integration tests need a real CKAN environment")
pytest.importorskip("ckanext.harvest", reason="integration tests need ckanext-harvest installed")
