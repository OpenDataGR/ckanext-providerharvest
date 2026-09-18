# ckanext-providerharvest

A self-service CKAN extension letting data providers register their own
private APIs, SFTP/SCP servers, or FTP/FTPS servers so their data can be
pulled into [data.gov.gr](https://data.gov.gr) on a schedule they control,
stored via `ckanext-datastore` (API sources) or as CKAN resources (file
sources), and served to consumers through DataStore's own query API.

See [DESIGN.md](DESIGN.md) for the full design, rationale, and rollout plan.

## Status

**Phase 1 (MVP) and Phase 1.5 (file transports) complete.** Implemented so far:

- `GenericProviderHarvester` -- one harvester class, config-driven (see
  `ckanext/providerharvest/harvesters/base_generic.py`).
- `DirectHTTPSTransport` -- HTTP JSON APIs, paginated, `delivery_mode=api_records`
  (per-record field mapping into DataStore).
- `SFTPTransport`, `ScpTransport`, and `FTPTransport` (Phase 1.5) --
  `delivery_mode=bulk_file`: whole remote files (glob-filtered) are loaded
  straight into CKAN resources via `loaders/file_resource_loader.py`, with
  ckanext-xloader's existing automatic resource-create/update hook doing the
  actual DataStore load -- no manual trigger needed.
  - SFTP/SCP (`transport/sftp.py`, `transport/scp.py`) share the exact same
    paramiko connection/auth code (`transport/ssh_common.py`) and SSH
    host-key pinning: fetched via `provider_source_fetch_host_key` at
    registration time (trust-on-first-use), re-verified on every later
    connection, refuses on any mismatch. SFTP streams files directly (a
    real random-access file handle); SCP has no equivalent (whole-file
    push/pull protocol, and its `list_entries` has no native
    directory-listing call either, so it runs a single
    fixed/parameterized `find` command over an exec channel instead,
    path/glob shell-quoted and restricted to a safe character set).
  - FTP (`transport/ftp.py`) defaults to FTPS (explicit TLS, a real
    verifying `ssl.create_default_context()` -- never disabled) with
    the data channel secured too, not just login; plain FTP is refused
    unless the source was registered with an explicit
    `plain_ftp_acknowledged` opt-in. Lists via MLSD (RFC 3659).
  - SCP and FTP both lack a true streamed-read primitive (unlike SFTP),
    *and* CKAN's own resource uploader requires a seekable handle (it
    measures file size by seeking) -- so both download to a throwaway
    local temp file first (`transport/_local_download.py`), deleted the
    moment the caller is done with it.
- `ApiKeyAuth` -- the only HTTP auth strategy implemented so far (irrelevant
  for SFTP/SCP/FTP, which authenticate from the secret's own shape --
  username + password or private key -- instead of the `AuthStrategy`
  interface).
- `EnvelopeSecretsBackend` -- AES-256-GCM envelope encryption for provider
  credentials, master key outside the CKAN database.
- Network-target validator (`logic/validators.py`) -- the SSRF-class
  defense; rejects loopback/link-local/private/reserved targets by
  default, re-validated on every connection attempt.
- `DataStoreLoader` -- pushes mapped rows into `ckanext-datastore` via
  `datastore_create`/`datastore_upsert`.
- Provider self-service actions (`logic/action.py`): `provider_source_create`,
  `provider_source_test_connection`, `provider_source_fetch_host_key`,
  `provider_source_activate` (the admin approval gate),
  `provider_source_list_mine`, all org-scoped via `logic/auth.py` rather
  than requiring CKAN sysadmin rights.
- Job-failure email notifications to the provider's registered contact
  after repeated consecutive failures (`notifications.py`).
- Self-service web UI (`blueprints/provider_ui.py`, Phase 2): a
  form-driven flow at `/provider-harvest/sources` (list) and
  `/provider-harvest/sources/new` (register + test-connection preview),
  plus a sysadmin approval queue at `/provider-harvest/admin/pending`.
  Covers all four transports: an HTTP API section, one shared "SFTP / SCP"
  section (a subsystem sub-selector picks which, since they're identical
  fields otherwise -- port/remote_path/glob, username + password-or-
  private-key auth, and the "Fetch host key" trust-on-first-use step), and
  an "FTP / FTPS" section (with the `use_tls`/plain-FTP-acknowledgment
  checkboxes). Field-mapping rows are plain repeatable form fields for
  now, not a dynamic JS editor -- see the blueprint's module docstring.

**Not yet built:** Basic/OAuth2/mTLS auth, and the broker/relay transport
(Phase 1.5 stretch items DESIGN.md marks as stub-only); a real dynamic
field-mapping editor and richer approval-workflow UI (Phase 2 follow-ons).

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
in the extension itself is mocked.

The `bulk_file` half is likewise automated for all three file transports,
each against a real test server (not a fake) seeded with a fixed set of
files (`export-2024.csv`, `export-2023.csv`, and a `readme.txt` the
`glob_pattern="*.csv"` filter must exclude): registers via
`provider_source_fetch_host_key` (SFTP/SCP only) + `provider_source_create`,
activates, runs gather/import_stage, and confirms the glob-matched files
land as CKAN resources.

- [`test_e2e_sftp_harvest.py`](ckanext/providerharvest/tests/integration/test_e2e_sftp_harvest.py)
  against [`docker/sftp-provider/`](docker/sftp-provider/upload/) (`atmoz/sftp`,
  an OpenSSH server locked to the SFTP subsystem).
- [`test_e2e_scp_harvest.py`](ckanext/providerharvest/tests/integration/test_e2e_scp_harvest.py)
  against [`docker/scp-provider/`](docker/scp-provider/), a plain OpenSSH server
  built from scratch here -- `atmoz/sftp` specifically forces
  `ForceCommand internal-sftp`, which blocks the raw `scp`/exec-channel
  commands `ScpTransport` needs, so it can't be reused for this.
- [`test_e2e_ftp_harvest.py`](ckanext/providerharvest/tests/integration/test_e2e_ftp_harvest.py)
  against [`docker/ftp-provider/`](docker/ftp-provider/) (ProFTPD, plain FTP
  only -- FTPS isn't exercised here since a self-signed cert would
  correctly fail `FTPTransport`'s real, verifying TLS context; that path
  is covered directly by `test_transport_ftp.py`'s fakes instead).

The self-service web UI is exercised the same way, through the real
`/provider-harvest/*` routes rather than the actions directly, in
[`test_blueprint.py`](ckanext/providerharvest/tests/integration/test_blueprint.py)
(including the SFTP/SCP subsystem sub-selector and the FTP form fields).

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
