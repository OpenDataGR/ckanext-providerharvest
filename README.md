# ckanext-providerharvest

A self-service CKAN extension letting data providers register their own
private APIs (and, in a later phase, SFTP/SCP/FTP endpoints) so their data
can be pulled into [data.gov.gr](https://data.gov.gr) on a schedule they
control, stored via `ckanext-datastore`, and served to consumers through
DataStore's own query API.

See [DESIGN.md](DESIGN.md) for the full design, rationale, and rollout plan.

## Status

**Phase 1 (MVP) in progress.** Implemented so far:

- `GenericProviderHarvester` -- one harvester class, config-driven (see
  `ckanext/providerharvest/harvesters/base_generic.py`).
- `DirectHTTPSTransport` -- the only transport implemented so far (HTTP
  JSON APIs, paginated). SFTP/SCP/FTP are Phase 1.5, not yet built.
- `ApiKeyAuth` -- the only auth strategy implemented so far.
- `EnvelopeSecretsBackend` -- AES-256-GCM envelope encryption for provider
  credentials, master key outside the CKAN database.
- Network-target validator (`logic/validators.py`) -- the SSRF-class
  defense; rejects loopback/link-local/private/reserved targets by
  default, re-validated on every connection attempt.
- `DataStoreLoader` -- pushes mapped rows into `ckanext-datastore` via
  `datastore_create`/`datastore_upsert`.
- Provider self-service actions (`logic/action.py`): `provider_source_create`,
  `provider_source_test_connection`, `provider_source_activate` (the
  admin approval gate), `provider_source_list_mine`, all org-scoped via
  `logic/auth.py` rather than requiring CKAN sysadmin rights.
- Job-failure email notifications to the provider's registered contact
  after repeated consecutive failures (`notifications.py`).

**Not yet built:** the self-service web UI/blueprint (actions above are
callable via CKAN's API today, no forms yet), SFTP/SCP/FTP transports,
Basic/OAuth2/mTLS auth, and the broker/relay transport.

## Development

Requires Python 3.9+ (CKAN 2.11 itself targets 3.9/3.10/3.11; this repo's
own venv here uses whatever is locally available for running the
CKAN-independent unit tests).

```bash
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate on Linux/macOS
pip install -e .[dev]
```

### Running tests

Most of this extension's logic (crypto, the SSRF-class validator, field
mapping, the HTTP transport, rate limiting, auth strategies, notification
building) has **no CKAN dependency** and is unit-tested directly:

```bash
pytest ckanext/providerharvest/tests
```

The model layer, `logic/action.py`, `logic/auth.py`, `harvesters/base_generic.py`,
and `plugin.py` import `ckan`/`ckanext.harvest` directly and can only be
exercised against a real CKAN instance. Per the design doc's Deployment
Model section, that verification happens against a **Docker Compose CKAN
2.11.3 replica** matching data.gov.gr's confirmed extension set
(`harvest`, `datastore`, `xloader`, `scheming_datasets`, `dcat`, ...) --
not against the live site, which this project has no direct access to.
That replica is not yet set up in this repo.

## Installing into a CKAN instance

1. `pip install -e .` (or from a built wheel) into the CKAN environment.
2. Add `providerharvest` to `ckan.plugins` in `ckan.ini`, after `harvest`
   and `datastore`.
3. Set `CKAN_PROVIDERHARVEST_MASTER_KEY` (base64-encoded 32 random bytes,
   e.g. `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`)
   in the environment CKAN runs under -- **outside** `ckan.ini` and outside
   the CKAN database.
4. Restart CKAN. The extension's tables are created automatically on
   startup (`model.meta.init_tables()`, called from `IConfigurable.configure`).
