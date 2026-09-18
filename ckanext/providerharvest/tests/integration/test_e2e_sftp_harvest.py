"""End-to-end SFTP/bulk_file harvest-job scenario against the docker/
replica's real ``sftp-provider`` container (a genuine OpenSSH server via
``atmoz/sftp``, see docker-compose.yml) -- Phase 1.5's counterpart to
test_e2e_harvest.py's HTTP/api_records flow. Exercises SFTPTransport's
real paramiko connect/host-key/auth/listing path against a real server,
not fakes (those already live in test_transport_sftp.py), plus the
provider_source_fetch_host_key -> provider_source_create ->
provider_source_activate -> gather/import_stage -> FileResourceLoader
chain end to end.

Same reasoning as test_e2e_harvest.py's module docstring for calling the
harvester stages directly rather than via the real queue: the
_allow_private_network fixture below only patches this pytest process,
not ckan-worker's separate one.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

from ckanext.providerharvest.harvesters.base_generic import GenericProviderHarvester

SFTP_ENDPOINT_URL = "sftp://sftp-provider"
SFTP_USERNAME = "produser"
SFTP_PASSWORD = "test-pass"


@pytest.fixture(autouse=True)
def _allow_private_network(monkeypatch):
    # sftp-provider's compose-network address is exactly what the real
    # SSRF-class validator exists to reject for a real registration (see
    # DESIGN.md) -- bypassed here the same way test_e2e_harvest.py does
    # for mock-provider, not by loosening anything in extension code.
    import ckanext.providerharvest.transport.sftp as transport_mod
    monkeypatch.setattr(
        transport_mod, "assert_safe_network_target",
        lambda host, port, allow_private_ranges=False: [host],
    )


def _dataset_defaults(name: str):
    return {
        "title_translated": {"en": name},
        "notes_translated": {"en": "SFTP integration test dataset for %s" % name},
    }


_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


#: Same "no clean_db" reasoning as test_e2e_harvest.py.
@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestFullSftpHarvestFlow:
    def test_register_activate_run_and_confirm_resources(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        sysadmin = factories.Sysadmin()
        editor_ctx = {"user": editor["name"], "ignore_auth": False}

        # Trust-on-first-use: fetch the real fingerprint before it can be
        # pinned via provider_source_create, exactly as the self-service
        # UI flow would (see provider_source_fetch_host_key).
        key_info = helpers.call_action(
            "provider_source_fetch_host_key", editor_ctx,
            owner_org=org["id"], endpoint_url=SFTP_ENDPOINT_URL,
        )
        assert key_info["fingerprint"].startswith("SHA256:")

        created = helpers.call_action(
            "provider_source_create",
            editor_ctx,
            name="sftp-mock-source",
            owner_org=org["id"],
            endpoint_url=SFTP_ENDPOINT_URL,
            transport_type="sftp",
            auth_type="ssh_credentials",  # inert for sftp -- see base_generic._build_transport
            credential_fields={"username": SFTP_USERNAME, "password": SFTP_PASSWORD},
            delivery_mode="bulk_file",
            remote_path="/upload",
            glob_pattern="*.csv",
            host_key_fingerprint=key_info["fingerprint"],
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("sftp-mock-source"),
        )
        harvest_source_id = created["harvest_source_id"]
        assert created["status"] == "pending"

        sysadmin_ctx = {"user": sysadmin["name"]}
        activated = helpers.call_action(
            "provider_source_activate", sysadmin_ctx, harvest_source_id=harvest_source_id
        )
        assert activated["status"] == "active"

        from ckanext.providerharvest.model import provider_source as provider_source_model
        provider_source = provider_source_model.get_by_harvest_source_id(harvest_source_id)
        assert provider_source.ckan_package_id
        # bulk_file sources never populate ckan_resource_id -- each
        # remote file becomes its own resource at import time instead
        # (see ensure_provider_resource's delivery_mode branch).
        assert provider_source.ckan_resource_id is None

        from ckanext.harvest.model import HarvestJob, HarvestObject, HarvestSource
        harvest_source_orm = HarvestSource.get(harvest_source_id)
        job = HarvestJob(source=harvest_source_orm)
        job.save()

        harvester = GenericProviderHarvester()
        object_ids = harvester.gather_stage(job)
        # upload/ has export-2024.csv, export-2023.csv, and readme.txt --
        # glob_pattern="*.csv" must exclude the .txt file.
        assert len(object_ids) == 2, "expected exactly the two .csv files, glob filtering failed"
        for object_id in object_ids:
            harvest_object = HarvestObject.get(object_id)
            assert harvester.fetch_stage(harvest_object)
            assert harvester.import_stage(harvest_object), harvest_object.errors

        package = helpers.call_action(
            "package_show", {"ignore_auth": True}, id=provider_source.ckan_package_id
        )
        resource_names = sorted(r["name"] for r in package["resources"])
        assert resource_names == ["export-2023.csv", "export-2024.csv"]
        assert all(r["format"] == "CSV" for r in package["resources"])

    def test_connect_rejects_a_host_key_that_was_never_pinned(self):
        org = factories.Organization()
        editor = factories.User()
        helpers.call_action(
            "organization_member_create",
            {"ignore_auth": True},
            id=org["id"], username=editor["name"], role="editor",
        )
        editor_ctx = {"user": editor["name"], "ignore_auth": False}

        with pytest.raises(Exception):
            helpers.call_action(
                "provider_source_create",
                editor_ctx,
                name="sftp-unpinned-source",
                owner_org=org["id"],
                endpoint_url=SFTP_ENDPOINT_URL,
                transport_type="sftp",
                auth_type="ssh_credentials",
                credential_fields={"username": SFTP_USERNAME, "password": SFTP_PASSWORD},
                delivery_mode="bulk_file",
                remote_path="/upload",
                glob_pattern="*.csv",
                # host_key_fingerprint deliberately omitted.
                frequency="MANUAL",
                dataset_defaults=_dataset_defaults("sftp-unpinned-source"),
            )
