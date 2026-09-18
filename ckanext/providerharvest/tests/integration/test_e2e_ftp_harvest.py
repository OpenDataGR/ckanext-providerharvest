"""End-to-end FTP/bulk_file harvest-job scenario against the docker/
replica's real ``ftp-provider`` container (vsftpd, plain FTP only -- see
docker/ftp-provider/Dockerfile for why FTPS isn't exercised here: a
self-signed cert would correctly fail FTPTransport's real, verifying TLS
context, and that path is already covered directly by
test_transport_ftp.py's fakes). Exercises the explicit-acknowledgment
plain-FTP path DESIGN.md's Phase 1.5 notes call for, against a real
vsftpd server -- MLSD listing and the real FTP data-connection transfer,
not fakes -- plus provider_source_create -> provider_source_activate ->
gather/import_stage -> FileResourceLoader end to end.

Same reasoning as test_e2e_harvest.py's module docstring for calling the
harvester stages directly rather than via the real queue.
"""

from __future__ import annotations

import pytest
from ckan.tests import factories, helpers

from ckanext.providerharvest.harvesters.base_generic import GenericProviderHarvester

FTP_ENDPOINT_URL = "ftp://ftp-provider"
FTP_USERNAME = "produser"
FTP_PASSWORD = "test-pass"


@pytest.fixture(autouse=True)
def _allow_private_network(monkeypatch):
    # Same reasoning as test_e2e_sftp_harvest.py: ftp-provider's
    # compose-network address is exactly what the real SSRF-class
    # validator exists to reject for a real registration. Patched
    # directly in transport.ftp -- FTP isn't SSH-based, doesn't go
    # through ssh_common.
    import ckanext.providerharvest.transport.ftp as ftp_mod
    monkeypatch.setattr(
        ftp_mod, "assert_safe_network_target",
        lambda host, port, allow_private_ranges=False: [host],
    )


def _dataset_defaults(name: str):
    return {
        "title_translated": {"en": name},
        "notes_translated": {"en": "FTP integration test dataset for %s" % name},
    }


_REQUIRED_PLUGINS = (
    "harvest datastore xloader scheming_datasets fluent dcat providerharvest"
)


@pytest.mark.ckan_config("ckan.plugins", _REQUIRED_PLUGINS)
@pytest.mark.usefixtures("with_plugins")
class TestFullFtpHarvestFlow:
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

        created = helpers.call_action(
            "provider_source_create",
            editor_ctx,
            name="ftp-mock-source",
            owner_org=org["id"],
            endpoint_url=FTP_ENDPOINT_URL,
            transport_type="ftp",
            auth_type="ftp_credentials",  # inert for ftp -- see base_generic._build_transport
            credential_fields={"username": FTP_USERNAME, "password": FTP_PASSWORD},
            delivery_mode="bulk_file",
            remote_path="/upload",
            glob_pattern="*.csv",
            # The test server is plain FTP only (see module docstring) --
            # this is the explicit, individually-flagged opt-in
            # DESIGN.md's Phase 1.5 notes require for that.
            use_tls=False,
            plain_ftp_acknowledged=True,
            frequency="MANUAL",
            dataset_defaults=_dataset_defaults("ftp-mock-source"),
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
        assert provider_source.ckan_resource_id is None
        assert provider_source.plain_ftp_acknowledged is True

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

    def test_create_rejects_plain_ftp_without_acknowledgment(self):
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
                name="ftp-unacknowledged-source",
                owner_org=org["id"],
                endpoint_url=FTP_ENDPOINT_URL,
                transport_type="ftp",
                auth_type="ftp_credentials",
                credential_fields={"username": FTP_USERNAME, "password": FTP_PASSWORD},
                delivery_mode="bulk_file",
                remote_path="/upload",
                glob_pattern="*.csv",
                use_tls=False,
                # plain_ftp_acknowledged deliberately omitted.
                frequency="MANUAL",
                dataset_defaults=_dataset_defaults("ftp-unacknowledged-source"),
            )
