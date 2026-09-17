# Provider Data ETL for data.gov.gr — Implementation Plan

**Immediate next step:** create a new private GitHub repository under the **OpenDataGR**
organization, named **`ckanext-providerharvest`**, with an initial commit containing this design
doc (as `README.md` or `DESIGN.md`) — code scaffolding is a separate, later step once the team is
ready to start implementation, not part of this initial commit.

## Context

data.gov.gr (Greece's national open data portal) runs **CKAN**. Data providers currently have no
way to connect their internal/private systems to the portal without exposing those systems
publicly. The goal: let a provider log into a self-service portal, register their private API or
SFTP endpoint plus credentials, pick a pull frequency, and have the system periodically fetch
their data, store it, show a preview, and expose it through a new consumer-facing API — without
the provider ever needing to make their system publicly reachable in an unrestricted way.

Key decisions already made with the user:
- Build as a **CKAN extension** (Python), not a standalone service — reuse CKAN's scheduling,
  auth, and organization model instead of rebuilding them.
- **Self-service** provider onboarding (providers configure their own connection), not
  admin-config-file only.
- MVP network transport: **IP-allowlisted endpoint** (provider firewalls their API/SFTP host to
  the harvester's static egress IP) + credential-based auth — not an outbound-agent/broker model.
  The connector is built so a broker/relay transport can be added later as a second transport type
  without reworking the harvester.
- Providers register **REST API and/or SFTP** endpoints, tokens, and their own pull **frequency**.
- The system must **store** the pulled data itself and **serve a new API** for consumers — not
  just register metadata pointing back at the provider's original (private) URL.

## Confirmed: Existing data.gov.gr Environment

Checked `https://data.gov.gr/api/3/action/status_show` directly. Findings that materially change
the risk profile of this plan:

- **CKAN 2.11.3**, with **`harvest`, `datastore`, and `xloader` extensions already installed and
  enabled** — the three hard dependencies this design assumed are already running in production.
  This resolves the biggest open question from earlier discussion: no new core infra to stand up.
- **Precedent for institution-specific harvesters already exists**: the extension list includes
  `bank_of_greece_harvester`, `attica_opendata_harvester`, `apd_kritis_harvester`,
  `ekan_dcat_harvester`, `core_ckan_harvester`, `custom_dcat_harvester`, `csw_harvester`,
  `wms_capabilities_harvester`, `oai_pmh_dcat_harvester`, `dkan_ckan_harvester` — one bespoke
  harvester extension per data source, admin-authored and deployed. **This plan's "one generic,
  self-service-configurable harvester" is a deliberate departure from that existing convention**,
  not an extension of it — necessary because the goal here is provider self-service, not
  admin-coded integrations, but worth flagging explicitly to whoever owns data.gov.gr's codebase,
  since it's a different pattern than what the team has built so far.
- **`scheming_datasets` / `scheming_organizations` / `dcat` / `hvd_validator` / `fluent` are
  already installed** — confirmed via public API (`scheming_dataset_schema_list` /
  `scheming_dataset_schema_show`, both reachable without contractor involvement). Three schema
  types exist: `dataset`, `data-service`, `decision`.
  - `dataset` fields: only `title_translated`/`notes_translated` (multilingual, via `fluent`) are
    required; everything else (`theme`, `hvd_category`, `access_rights`, `applicable_legislation`,
    `publisher`, `spatial_coverage`, `temporal_coverage`, etc.) is optional DCAT-AP-EL/HVD metadata.
    Resource-level fields include `format`, `license`, `access_url`/`download_url`, and —
    critically — **`access_services`**, which links a resource to a `data-service` record.
  - **`data-service` fields**: `title`, `name`, `endpoint_url` required; `endpoint_description`,
    `documentation`, `theme`, `hvd_category` optional. This is a DCAT `DataService` entity —
    exactly the mechanism to register "the API where consumers query this dataset" as a
    discoverable catalog entry, not just an un-cataloged DataStore endpoint.
  - **Plan change**: `import_stage` should, alongside `datastore_upsert`, also create/update a
    `data-service` record per provider source (`endpoint_url` = the DataStore `datastore_search`
    endpoint for that resource, or a stable wrapper URL) and set the resource's `access_services`
    field to link them. This makes the new consumer API a first-class, discoverable catalog entry
    — a stronger fit for "provide a new api for consumers" than relying on DataStore alone.
  - `FieldMappingProfile` targets these real fields directly — no more guessing at a generic
    DCAT-AP-EL list.
- **`keycloak` is installed** — provider login/authentication is likely federated through Keycloak
  SSO rather than plain local CKAN accounts. The self-service onboarding flow's "provider maps to
  a CKAN organization, staff are org editor/admin users" still holds, but identity/login itself
  should reuse the existing Keycloak integration, not add a separate auth mechanism.
- **`azurefilestore` / `cloudstorage` are installed** — resource file storage already goes to
  Azure Blob Storage via CKAN's standard resource-upload path. `loaders/file_resource_loader.py`
  (the SFTP bulk-file path) needs no new blob storage work — staging a downloaded file as a CKAN
  resource already lands it in Azure through the existing pipeline.
- **`tracking` / `stats` / `matomo` are installed** — if the earlier "consumer usage stats for
  providers" question gets a yes, this is likely surfaceable from existing analytics rather than
  needing new instrumentation.

Net effect: the "minimum intervention" question from before has a much more concrete answer now —
this extension installs alongside infrastructure that already exists and already runs comparable
harvester extensions in production, which meaningfully lowers integration risk. It still requires
its own new DB tables, a plugin-list config change, and a CKAN restart, same as any other
extension deploy on this instance.

## Deployment Model / Operational Constraint

data.gov.gr's live instance is operated by a **third-party contractor** — there is no direct
access to install, configure, or verify anything on the production system directly. This changes
how the work is built and delivered:

- **Development and verification happen against a local/staging CKAN replica** (Docker Compose,
  pinned to CKAN **2.11.3** with the same confirmed extension set — `harvest`, `datastore`,
  `xloader`, `scheming_datasets`, `dcat`, etc. — installed and enabled), not the live site. This is
  also the only realistic way to validate the plugin-list/config changes and DB migrations before
  anyone touches production.
- **The deliverable is a self-contained, installable package for the contractor to review and
  apply** — the `ckanext-providerharvest` extension itself, its DB migrations, a documented
  `ckan.ini` config diff (new plugin entry, any new settings), and clear integration/rollback
  instructions — not a live deployment performed by this project directly.
- Anything that needs confirming *about* the real instance (the actual `scheming_datasets` schema,
  how Keycloak maps users to organizations in practice, egress IP for allowlisting, etc.) has to be
  requested from the contractor as information/config, since it can't be inspected directly beyond
  what a public API like `status_show` already reveals.
- Practical effect on rollout: treat "handoff package ready + validated against the local replica"
  as this project's actual finish line for each phase, with the contractor's own install/verify
  step as a separate, later gate outside this plan's direct control.

## Architecture Overview

Built on two existing CKAN extensions rather than from scratch:

- **`ckanext-harvest`** — provides scheduling, job/queue infrastructure, and the harvest
  source/job/object data model. We plug in one generic harvester class.
- **`ckanext-datastore`** — CKAN's tabular data store. Once records are pushed into DataStore via
  `datastore_upsert`, CKAN automatically provides: a query API for consumers
  (`datastore_search`, `datastore_search_sql`) and a browsable data preview (recline/table view) —
  this satisfies "store them," "provide a new api for consumers," and "show them" without building
  bespoke storage or serving infrastructure. Custom API endpoints on top can be added later if
  DataStore's query model proves insufficient (e.g. deeply nested/non-tabular provider data).

New extension: **`ckanext-providerharvest`**, containing everything provider-specific: the generic
harvester, pluggable transports (HTTPS direct, SFTP), pluggable auth strategies, credential
storage, field mapping, and the self-service onboarding UI.

```
Provider registers ──> HarvestSource (ckanext-harvest) + ProviderSourceExtension (ours)
                              │
                     scheduled by ckanext-harvest
                              ▼
                 GenericProviderHarvester.gather_stage
                    (Transport + AuthStrategy pull data)
                              ▼
                 GenericProviderHarvester.import_stage
              (FieldMapper → datastore_upsert + package metadata)
                              ▼
        CKAN DataStore ──> consumer query API + preview (built-in)
```

## Module Layout

```
ckanext-providerharvest/ckanext/providerharvest/
├── plugin.py                  # IHarvester, IAuthFunctions, IBlueprint, IActions, IConfigurer
├── harvesters/
│   └── base_generic.py        # GenericProviderHarvester(HarvesterBase) — the one harvester class,
│                               #   branches on delivery_mode + transport_type, both data-driven
├── transport/
│   ├── base.py                # Transport ABC: connect()/list_entries(cursor)/open_entry(ref)/close()
│   ├── direct_https.py        # MVP: allowlisted-IP HTTPS, mTLS/API-key aware, rate-limited
│   ├── sftp.py                # MVP: paramiko-based SFTP, host-key pinned, streamed file reads
│   ├── scp.py                 # MVP: paramiko-based SCP (exec channel), shares SSH host-key pinning
│   │                          #   with sftp.py; for servers exposing only the scp command, not the
│   │                          #   SFTP subsystem
│   ├── ftp.py                 # MVP: ftplib-based FTP/FTPS, for providers without SFTP available
│   └── broker.py              # Phase 2 stub — same interface, not implemented yet
├── parsers/                    # bytes -> structured record dicts (decoupled from transport)
│   ├── base.py                 # RecordParser ABC: parse(stream) -> Iterator[dict]
│   ├── json_records.py         # JSON array / JSON-lines
│   ├── csv_parser.py           # streaming csv.DictReader — SFTP flat-file dumps
│   └── xml_parser.py
├── loaders/                    # structured records -> stored + queryable data
│   ├── datastore_loader.py     # datastore_create (schema) + batched datastore_upsert
│   └── file_resource_loader.py # stage file as CKAN resource, delegate bulk load to ckanext-xloader
├── auth_strategies/
│   ├── base.py                 # AuthStrategy ABC
│   ├── api_key.py               # header/query param key
│   ├── basic_auth.py
│   └── ssh_key.py               # password or private-key auth for SFTPTransport
├── secrets/
│   ├── base.py                  # SecretsBackend ABC: put()/get()/rotate()/delete()
│   └── envelope.py              # MVP backend: envelope encryption (see below)
├── model/
│   ├── provider_source.py       # ProviderSourceExtension: + host_key_fingerprint, delivery_mode
│   ├── field_mapping.py         # FieldMappingProfile: + primary_key_fields, declared_field_types
│   └── audit_log.py             # OutboundRequestLog
├── logic/
│   ├── auth.py                  # org-scoped permissions (not sysadmin-only, unlike stock harvest UI)
│   ├── action.py                # provider_source_create/list_mine/test_connection, mapping_preview
│   ├── schema.py                 # onboarding form validation schemas
│   └── validators.py             # network-target validator (HTTP URLs + SFTP host:port), security-critical
├── blueprints/provider_ui.py     # Flask blueprint: self-service routes
└── templates/providerharvest/    # source_form, source_list, mapping_editor, test_connection_result
```

`setup.py` install_requires now includes `ckanext-datastore` and `ckanext-xloader` (both required,
see Storage section below) plus `paramiko` for SFTP.

## Connector Abstraction

**One harvester class** (`GenericProviderHarvester`), driven entirely by per-source config — not
one class per provider or per transport. It branches on two config-driven values:
- `transport_type`: `http` | `sftp` | `scp` | `ftp` | (phase 2) `broker`.
- `delivery_mode`: `api_records` (small JSON records via pagination — the typical HTTP-API shape)
  vs. `bulk_file` (whole files to download and bulk-load — the typical SFTP/flat-file-export shape).

**Broadened `Transport` ABC** — same interface for HTTP and SFTP so `gather_stage` never branches
on transport type directly:
- `list_entries(cursor)`: HTTP returns a page of API records; SFTP returns a page of file
  references (path/mtime/size), diffed against the last run so unchanged files aren't re-pulled.
- `open_entry(entry)`: HTTP returns the record's bytes (or sub-fetches a linked file); SFTP opens
  a **streamed** file handle (no full-file in-memory load — required for multi-GB dumps).

- `DirectHTTPSTransport`: `requests`-based, TLS verification always on (custom CA bundle allowed,
  never a "skip verification" toggle), mTLS cert support, token-bucket rate limiting, exponential
  backoff on 429/5xx, every call logged to `OutboundRequestLog`.
- `SFTPTransport`: paramiko-based, **pins the server's host-key fingerprint at registration time**
  and verifies it on every connection (SSH has no CA hierarchy, so trust-on-first-use-with-pinning
  is the standard safe pattern here — the SSH analogue of TLS chain validation; never expose a
  "skip host key check" option), lists a configured remote directory filtered by glob/regex,
  streams files for chunked parsing.
- `ScpTransport` (NEW, for providers whose SSH server only exposes the `scp` command, not the
  SFTP subsystem — some legacy/hardened systems do this): reuses the exact same SSH connection and
  host-key pinning/verification code as `SFTPTransport` (same trust model, same security
  requirement). Differs in mechanics: SCP has no native directory-listing operation, so
  `list_entries` runs a constrained remote command (e.g. `ls`/`find` over an exec channel, output
  parsed defensively) to enumerate files before copying them individually — treat the remote
  command string as untrusted-input-adjacent and keep it fixed/parameterized (path + glob only,
  never provider-supplied shell text passed through unescaped).
- `FTPTransport` (NEW, for providers whose only file-transfer option is legacy FTP, not SFTP):
  `ftplib`-based, same `list_entries`/`open_entry` shape (directory listing filtered by
  glob/regex, streamed file reads). **Defaults to FTPS (`FTP_TLS`, explicit AUTH TLS)**, since
  plain FTP sends credentials and data unencrypted — a real conflict with every other
  never-disable-verification rule in this design. Plain (non-TLS) FTP is supported only as an
  explicit, individually-flagged opt-in per source (e.g. a checked "I understand this provider's
  connection is unencrypted" acknowledgment at registration, logged), for cases where a provider
  genuinely has no FTPS/SFTP capability — not the default, and called out to whoever approves the
  source (see approval gate) so it's a conscious, visible risk acceptance rather than a silent one.
- `BrokerTransport` (stub only): keeps the interface stable for a future provider-side agent that
  connects outbound to a broker, for providers who can't allowlist an IP. Selectable-but-disabled
  in the onboarding UI now.

**`gather_stage` / `import_stage`, by delivery mode:**
- `api_records`: `gather_stage` creates one `HarvestObject` per record (content = the raw JSON,
  small enough to store inline). `import_stage` maps fields, then `loaders/datastore_loader.py`
  calls `datastore_create` once (schema from the mapping profile's declared field types) and
  batches records through `datastore_upsert` (chunked, e.g. 1,000–5,000 rows/call, with a
  last-successful-chunk marker so a failed job resumes rather than restarts).
- `bulk_file`: `gather_stage` creates one `HarvestObject` per **file reference** (not the file's
  bytes, to avoid bloating the harvest DB with multi-GB payloads). `import_stage` streams the file
  down into CKAN's own resource storage, creates/updates a CKAN resource pointing at it, and hands
  off to `loaders/file_resource_loader.py`, which triggers **`ckanext-xloader`** — CKAN's existing
  bulk-load extension (COPY-based, much faster than row-by-row upserts for large files) — instead
  of hand-rolling CSV parsing/type-guessing/bulk insert.

**Auth strategies**: API key (header/query param), Basic auth, SSH password/private-key (all
resolved through the same `SecretsBackend`, no special-casing per transport). OAuth2
client-credentials and mTLS can follow in phase 2 if a candidate provider needs them.

**Pagination/incremental sync**: config-driven (`page_number`, `cursor`/`next_link`, `offset_limit`
for HTTP; mtime/size diff for SFTP), with a hard server-side max-records/max-files/max-bytes cap
per run regardless of provider config — bounds worst-case load and doubles as a resource-exhaustion
control against an oversized or malicious file.

**Field mapping** (`FieldMappingProfile` model): JSONPath/XPath/CSV-column rules mapping provider
fields to CKAN package fields, **plus declared field types and primary-key field(s)** — both are
now required inputs (not just nice-to-have) since `datastore_create`/`datastore_upsert` need an
explicit schema and an upsert key; inferring types at runtime against a live/moving API is fragile.
Validated against an injection-safe parser (e.g. `jsonpath-ng`, not `eval`-based). The self-service
"Test Connection" action fetches one real sample record/file (for `bulk_file`, just lists matching
files and samples a few rows — never a full download) and previews the mapping result before the
provider activates the source.

## Storage & Consumer API

**Reuse `ckanext-datastore` for storage/query, `ckanext-xloader` for bulk file loads — don't build
bespoke storage or a bespoke API.** This is a hard dependency this design now assumes is installed
on data.gov.gr's CKAN instance (flagged as an open question below if it isn't yet).

- Once a resource is DataStore-backed, CKAN's stock resource view (grid/table preview) renders a
  browsable, sortable, filterable preview automatically — satisfies "show them" with no new UI.
- `datastore_search` (structured filter/sort/paginate) is the default consumer-facing query API,
  free once rows exist. `datastore_search_sql` (read-only SQL) is more powerful but a bigger attack
  surface — CKAN gates it behind `ckan.datastore.sqlsearch.enabled`; **decide per-deployment
  whether to enable it for data.gov.gr, or restrict it to vetted/rate-limited consumers**, rather
  than turning it on by default.
- **Where this doesn't fit well** (explicit trade-off, not silently papered over): deeply
  nested/non-tabular provider payloads need flattening (default approach) or an escape-hatch JSON
  column that `datastore_search` can't filter inside of; very high-QPS consumer access may need a
  caching layer CKAN doesn't provide out of the box; cross-dataset joins aren't supported by
  DataStore at all. Treat these as phase-2/case-by-case, not MVP blockers — but a conscious choice.

## Credential Storage

**Envelope encryption for MVP** (not Vault) — real security without new infra to operate; the
`SecretsBackend` ABC means swapping in Vault/cloud KMS later is a config change, not a rewrite.

- Master key lives outside the CKAN DB (env var / mounted secret file), never in `ckan.ini`.
- On save: generate a random data key (DEK) → AES-256-GCM encrypt the credential → wrap the DEK
  with the master key (KEK) → store both in a `providerharvest_secret` table.
- `HarvestSource.config` stores only `{"secret_ref": "<id>", ...}` — never plaintext, never even
  the wrapped key.
- Secrets are resolved in-memory only at harvest run time, discarded immediately after; never
  logged (regression test asserts serialized job/log output never contains fixture secret values).
- Rotation (`SecretsBackend.rotate`) re-wraps under a fresh DEK; a master-key (KEK) rotation
  CLI command re-wraps all rows — build this from day one since it's a compliance expectation.

## Self-Service Onboarding Flow

Reuses CKAN's existing org/role model instead of a new identity system:
- Each provider maps to a CKAN **Organization**; provider staff are org `editor`/`admin` users.
- New `logic/auth.py` overrides replace stock `ckanext-harvest`'s sysadmin-only checks with
  org-membership checks, so providers manage only their own org's sources.
- Flow: provider logs in → `/provider-harvest/sources` (own sources only) → "New Source" wizard
  (endpoint + transport type → auth → field mapping, using a live sample via Test Connection →
  frequency) → **Test Connection** runs a bounded dry run (no `HarvestObject`/package/DataStore
  writes) to validate connectivity and preview the mapping → on save, creates a `HarvestSource`
  (via CKAN's own `harvest_source_create` action, so org-scoping is enforced by CKAN itself) plus
  a `ProviderSourceExtension` row for provider-specific state.
- Endpoint step branches on transport type: HTTP (base URL, pagination) vs. **SFTP** (host, port,
  remote path/glob pattern, plus a host-key-confirmation sub-step — show the presented fingerprint,
  require the provider to confirm it, then pin it).
- Field-mapping step also collects **declared field types** and **primary-key field(s)** per
  mapped field — needed by `datastore_create`/`datastore_upsert`, not just the package dict.
- Registration also collects a **notification contact** (email, optionally a webhook URL) — since
  the provider owns their registration, they own responding to its failures too. A job-failure
  notification step in the harvester fires to this contact (after N consecutive failures, to avoid
  noise on a single transient blip), with a specific, actionable message (timeout vs. auth failure
  vs. mapping error) rather than a generic alert.
- **Approval gate**: a newly registered source starts `pending` and requires a data.gov.gr admin
  to flip it to `active` before its first scheduled run — given the harvester reaches into
  providers' semi-private networks, a human review checkpoint on the target endpoint is worth the
  friction. `gather_stage` checks this status and no-ops if not yet approved.

## Security

- **Network-target validation (highest-risk item)**: a provider-supplied endpoint — HTTP URL or
  SFTP host:port — is a target the harvester will connect to on a schedule with a real credential
  attached. `logic/validators.py` must reject loopback/link-local/cloud-metadata targets for both
  transport types, non-http(s) schemes for HTTP, and re-validate at both registration time and
  every scheduled run (DNS can rebind between the two).
  *Assumption to confirm with the user*: providers' endpoints are reachable over the public
  internet but firewalled to the harvester's egress IP (not raw RFC1918 addresses reached over a
  private VPN/peering) — if the latter is actually the intended topology, the validator's
  allow/deny ranges need to be adjusted per-source rather than globally denying private ranges.
- **TLS**: verification always on for HTTP; only a custom CA bundle path is configurable, never a
  "disable verification" toggle.
- **SSH host key pinning**: fingerprint recorded at registration, verified on every SFTP
  connection — the SSH analogue of TLS validation; never expose a "skip host key check" option.
- **Plain FTP is a deliberate, visible exception, not a default**: FTPS is the default for the FTP
  transport; unencrypted FTP is only usable per-source via an explicit acknowledged opt-in,
  reviewed at the approval gate, given it exposes credentials and data in transit otherwise.
- **Rate limiting**: client-side token bucket before sending, not just reactive 429 handling —
  protects what may be modestly-provisioned internal provider systems.
- **Audit logging**: every outbound request/connection logged (host, path minus querystring,
  status, timing) — never headers/body/credentials.
- **Resource exhaustion**: streamed (not full-in-memory) reads for SFTP files, chunked/batched
  DataStore loads, and a hard max file-size/total-bytes cap per job — bounds worst-case load from
  an oversized or malicious file, not just a performance concern.
- **`datastore_search_sql` exposure**: if enabled for consumers, confirm DataStore's underlying
  Postgres role is actually configured read-only (defense in depth beyond the Action API check).

## Rollout

**Phase 1 (MVP):** the smallest slice that proves the *whole* corrected vision end-to-end —
pull → store → serve → show — using only the HTTP path:
1. `GenericProviderHarvester`, `DirectHTTPSTransport` only, API-key auth only.
2. `delivery_mode = api_records`: parse JSON records → `FieldMapper` (with declared field types +
   primary key) → `datastore_create` + batched `datastore_upsert` via `loaders/datastore_loader.py`.
3. Envelope-encryption `SecretsBackend`.
4. Proven against one real/staging provider via CKAN's *stock* harvest-source admin UI first —
   self-service blueprint deferred — to validate the core pipeline cheaply.
5. Network-target validator (HTTP URLs) and audit logging — not deferrable.
6. Job-failure notification (email) to the provider's registered contact — not deferrable, given
   the explicit intent that providers own responding to their own source's problems.

**Phase 1.5:** `SFTPTransport` (+ host-key pinning) first, then `ScpTransport` (reusing the same
host-key/SSH plumbing) and `FTPTransport` (FTPS default) as thin follow-ons once the shared
`delivery_mode = bulk_file` + `ckanext-xloader` integration via `loaders/file_resource_loader.py`
is proven — validated against one real/staging provider per transport — file-transfer + bulk-load
correctness is a distinct risk
surface from the HTTP+direct-DataStore path, worth its own validation pass before self-service UI.

**Phase 2:** Self-service blueprint/forms (delivery-mode-aware fields, host-key confirmation step,
mapping editor), Basic/OAuth2/mTLS auth strategies, broker/relay transport for providers who can't
allowlist an IP, Vault-backed `SecretsBackend`, approval-workflow admin UI, richer DCAT-AP-EL
mapping presets (confirm data.gov.gr's actual DCAT-AP-EL field/vocabulary requirements before
finalizing the mapping schema), and a decision on `datastore_search_sql` exposure policy.

## Open Questions to Resolve Before/During Build

**Resolved via `status_show`:** `ckanext-datastore`/`ckanext-xloader`/`ckanext-harvest` are already
installed — no new core infra to provision.

**Resolved via public API (no contractor needed):** the real `dataset` and `data-service` scheming
schemas were pulled directly (see Confirmed Environment section) — `mapping.py` can target actual
fields now, including registering a `data-service` entry as the consumer API's catalog listing.

**Still open (genuinely need the contractor):**
1. Confirm how Keycloak-authenticated provider users map to CKAN organizations/roles in practice
   on this instance, so the onboarding flow's permission checks align with the real setup.
2. The harvester's egress IP(s) for providers to allowlist, and whatever process exists for
   getting a new CKAN extension + DB migration reviewed and scheduled for install.
3. Network topology: public-internet-with-IP-allowlist, or private VPN/peering reaching genuinely
   RFC1918 provider addresses? Changes the network-target validator's allow/deny logic.
4. Policy for non-tabular/deeply-nested provider payloads — flatten by default, or is DataStore a
   poor fit for some expected providers from day one?
5. Expose `datastore_search_sql` to consumers, or structured `datastore_search` only?
6. Fallback behavior when a provider's records have no stable primary key (full-table-refresh per
   run vs. requiring one) — needs a defined default, not left ambiguous.
7. Rough expected SFTP file sizes/frequency from real candidate providers — affects whether
   streaming + xloader alone is sufficient or more aggressive resumable-load logic is needed sooner.
8. Approval-workflow policy (admin sign-off before first live run, recommended) and role
   granularity (can any org editor create/edit sources, or only org admins activate them?).
9. Confirmed: provider run-failure notifications are wanted (email at minimum) — add an email/
   webhook field to source registration and a post-job-failure notification step to the harvester
   (not deferrable to phase 2, given the user's explicit "they should handle the problems" intent).
   Still open: consumer-facing usage stats for providers — likely feasible via the existing
   `tracking`/`stats`/`matomo` extensions if wanted, but not yet confirmed as in-scope.

## Verification

- Unit tests per module: `test_harvester.py` (gather/import stages against a mocked transport),
  `test_auth_strategies.py`, `test_transport.py` / `test_transport_sftp.py` (rate-limit/backoff,
  streamed reads), `test_ssrf_validator.py` (loopback/link-local/metadata/redirect cases for both
  HTTP and SFTP targets), `test_loaders_datastore.py`, `test_onboarding_flow.py` (org-scoped
  permissions, test-connection dry run never writes data).
- End-to-end: a **Docker Compose CKAN 2.11.3 replica** matching data.gov.gr's confirmed extension
  set (`harvest`, `datastore`, `xloader`, `scheming_datasets`, `dcat`, etc.) is the actual test
  environment, since production isn't directly reachable for verification. Register one mock HTTP
  provider and one mock SFTP provider (local test server/SFTP container), run a harvest job,
  confirm rows appear in DataStore and are queryable via `datastore_search`, confirm the resource
  preview renders, confirm a provider-scoped user cannot see or edit another org's source.
- Final deliverable check: the handoff package (extension + migrations + config diff + install
  instructions) applies cleanly to a *fresh* copy of that same replica from scratch — proves the
  contractor can actually follow it against their real instance.

## Critical Files

- `harvesters/base_generic.py` — core gather/import data flow, branching on `delivery_mode` and
  `transport_type`; everything else plugs into it.
- `loaders/datastore_loader.py` and `loaders/file_resource_loader.py` — where "store the data via
  DataStore/xloader instead of building bespoke storage" actually happens.
- `transport/sftp.py` (and the host-key pinning it shares with `transport/scp.py`) — this
  correctness/security logic is reused by two of the four transports.
- `secrets/envelope.py` — credential-at-rest security boundary, shared by all transports' secrets.
- `logic/validators.py` — network-target validation (HTTP + SFTP); highest-risk security-critical
  code in the design.
- `logic/auth.py` — org-scoped self-service permission model.
- `transport/base.py`, `transport/direct_https.py` — pluggable transport interface, must stay
  stable to support a future broker transport without rework.
