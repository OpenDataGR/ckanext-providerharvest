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
- Self-service web UI (`blueprints/provider_ui.py`, Phase 2): a
  form-driven flow at `/provider-harvest/sources` (list) and
  `/provider-harvest/sources/new` (register + test-connection preview),
  plus a sysadmin approval queue at `/provider-harvest/admin/pending`.
  Field-mapping rows are plain repeatable form fields for now, not a
  dynamic JS editor -- see the blueprint's module docstring.

**Not yet built:** SFTP/SCP/FTP transports, Basic/OAuth2/mTLS auth, and
the broker/relay transport (Phase 1.5); a real dynamic field-mapping
editor and richer approval-workflow UI (Phase 2 follow-ons).

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
That replica lives under [`docker/`](docker/); see "Local Docker replica"
below for how to bring it up.

### Local Docker replica

`docker/` brings up CKAN 2.11.3 plus the confirmed data.gov.gr extension
set (`harvest`, `datastore`, `xloader`, `scheming_datasets`, `dcat`,
`fluent`) and this extension itself, built from this repo checkout, with
`providerharvest` enabled after `harvest`/`datastore` per the install
instructions below. Requires Docker Engine with the Compose v2 plugin
(`docker compose`, not the standalone `docker-compose`).

```bash
cd docker
cp .env.example .env      # edit CKAN_PROVIDERHARVEST_MASTER_KEY etc. if needed
docker compose build
docker compose up -d
```

Five services come up: `ckan` (the web app), `ckan-worker` (the
ckanext-harvest gather/fetch queue consumers, a `harvester run`
scheduler loop, and the `ckan jobs worker` process xloader's bulk loads
actually run in -- see `docker/ckan/worker-entrypoint.sh`), `db`
(Postgres, with the separate `datastore_ro` read-only role
`ckanext-datastore` needs), `solr`, and `redis`.

Wait for `ckan` to report healthy (`docker compose ps`), then verify:

- **Plugins enabled**: `curl http://localhost:5000/api/3/action/status_show`
  should list `harvest`, `datastore`, `xloader`, `scheming_datasets`,
  `dcat`, `fluent`, and `providerharvest` under `extensions`.
- **This extension's tables exist** (created automatically via
  `model.meta.init_tables()`, called from `IConfigurable.configure`):

  ```bash
  docker compose exec db psql -U postgres -d ckandb -c '\dt providerharvest_*'
  ```

  Expect `providerharvest_provider_source`,
  `providerharvest_field_mapping_profile`, `providerharvest_secret`, and
  `providerharvest_outbound_request_log`.
- **The plugin loads without a traceback**: CKAN loads all enabled
  plugins at process start, before it serves any requests -- so the
  `status_show` check above only succeeding at all (and the `ckan`
  container's healthcheck going green) already proves
  `ckanext.providerharvest` imported and `configure()` ran cleanly. To
  double-check directly, `docker compose logs ckan` during startup
  should show no traceback mentioning `ckanext.providerharvest`.

CKAN's admin UI/API is at `http://localhost:5000` (sysadmin login from
`CKAN_SYSADMIN_NAME`/`CKAN_SYSADMIN_PASSWORD` in `.env`). Solr is
reachable at `http://localhost:8983/solr/ckan` for debugging the search
index.

To tear down (keeping the `.env`-configured data volumes):

```bash
docker compose down
```

To also wipe the Postgres/Solr/storage volumes (start completely fresh):

```bash
docker compose down -v
```

The `.env.example` values are fixed, non-secret local-dev placeholders
(including the envelope-encryption master key) checked in so a fresh
clone reproduces the same replica -- rotate all of them before adapting
this compose file for anything beyond a disposable local instance.

Full functional harvest-job testing against a mock HTTP provider (register
a source, run a job, confirm rows land in DataStore, org-scoping) is
automated in
[`ckanext/providerharvest/tests/integration/test_e2e_harvest.py`](ckanext/providerharvest/tests/integration/test_e2e_harvest.py),
run as part of the same `pytest ckanext/providerharvest/tests` command
above (a `conftest.py` skips this subdirectory when `ckan` isn't
importable, so it's a no-op outside this replica). It exercises the real
gather/fetch/import pipeline against
[`docker/mock-provider/`](docker/mock-provider/), a throwaway HTTP JSON
service, and the already-running `ckan-worker` queue consumers -- nothing
in the extension itself is mocked. The SFTP/SCP/FTP half of DESIGN.md's
"Verification" scenario still needs those transports to exist first
(Phase 1.5).

**CI**: none of this project's own dev machines have Docker available,
so the boot-and-verify steps above (build, bring up, confirm plugins +
tables, run the pytest suite inside the `ckan` container) run instead in
[`.github/workflows/docker-replica.yml`](.github/workflows/docker-replica.yml)
on GitHub's hosted runners, on PRs touching `docker/`,
`ckanext/providerharvest/`, or `setup.py`, and on demand via
`workflow_dispatch`. Since that pytest step covers
`ckanext/providerharvest/tests` as a whole, it includes the functional
harvest-job scenario above too.

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
