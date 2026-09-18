from setuptools import find_namespace_packages, setup

setup(
    name="ckanext-providerharvest",
    version="0.1.0",
    description=(
        "Self-service CKAN harvester extension letting data providers "
        "register private APIs / SFTP / SCP / FTP sources for ingestion "
        "into data.gov.gr, stored via ckanext-datastore and served "
        "through DataStore's consumer query API."
    ),
    packages=find_namespace_packages(include=["ckanext*"]),
    # Non-.py files aren't picked up by `packages=` alone -- irrelevant
    # for this project's own install path (`pip install -e .`, an
    # editable install that reads straight from the checkout, no
    # copying), but a real `pip install` from a built wheel needs these
    # listed explicitly or it silently ships an extension with no
    # templates and a migration directory missing its non-Python files
    # (alembic.ini, script.py.mako) -- `ckan db upgrade -p
    # providerharvest` would then fail to find them at all.
    package_data={
        "ckanext.providerharvest.templates.providerharvest": ["*.html"],
        "ckanext.providerharvest.migration.providerharvest": ["*.ini", "*.mako"],
    },
    install_requires=[
        # ckanext-harvest, ckanext-datastore, and ckanext-xloader are
        # already installed/enabled on data.gov.gr (confirmed via
        # status_show) and are assumed present in the target environment,
        # not pinned here to avoid conflicting with the site's own pins.
        "requests>=2.31",
        "cryptography>=42.0",
        "jsonpath-ng>=1.6",
        "paramiko>=3.4",  # SFTP/SCP transports, phase 1.5
        "scp>=0.15",  # ScpTransport -- thin SCP-protocol wrapper over paramiko.Transport
    ],
    extras_require={
        "dev": ["pytest>=8.0"],
    },
    entry_points={
        "ckan.plugins": [
            "providerharvest = ckanext.providerharvest.plugin:ProviderHarvestPlugin",
        ],
    },
)
