#!/bin/bash
# Entrypoint for the `ckan-worker` service: everything ckanext-harvest and
# ckanext-xloader need running as long-lived background processes, which
# `ckan/ckan-base`'s own start_ckan.sh (uwsgi) does not provide.
#
# Runs, in parallel, in this one container:
#   - `ckan harvester gather-consumer` / `fetch-consumer` -- the two
#     ckanext-harvest queue consumers that actually run harvest jobs
#     (README: "a worker/cron process for ckanext-harvest job processing").
#   - `ckan jobs worker` -- CKAN's RQ worker; this is where
#     ckanext-xloader's bulk-load jobs actually execute (xloader enqueues
#     via RQ from the web process, but the job body runs here).
#   - a simple scheduler loop calling `ckan harvester run` periodically,
#     standing in for the cron entry ckanext-harvest's own docs recommend
#     (`*/15 * * * * ckan harvester run`) -- a loop instead of real cron
#     so this one container needs no separate cron daemon.
#
# If any one of these dies, the whole container exits non-zero so
# Docker's restart policy restarts it cleanly, rather than silently
# limping along with only some of the queue consumers still alive.
set -euo pipefail

CKAN_CLI=(ckan -c "${CKAN_INI}")
# Always the ckan service's in-network address, never CKAN_SITE_URL --
# that's the public-facing browser URL (http://localhost:5000 in
# .env.example), which from inside this container means itself, not the
# ckan container. Using it here made this wait loop poll nothing,
# forever -- silently stalling every process below it (harvest
# consumers, ckan jobs worker, sysadmin creation) for the container's
# entire lifetime, with nothing surfacing the failure since the loop
# never exits with an error either.
CKAN_WEB_URL="http://ckan:5000"
HARVEST_RUN_INTERVAL_SECONDS="${HARVEST_RUN_INTERVAL_SECONDS:-60}"

echo "[worker] waiting for the CKAN web service (${CKAN_WEB_URL}) to answer status_show..."
until wget -qO- "${CKAN_WEB_URL}/api/3/action/status_show" >/dev/null 2>&1; do
    sleep 3
done
echo "[worker] CKAN web service is up."

# Nothing in the base ckan-base image actually creates a sysadmin from
# CKAN_SYSADMIN_NAME/PASSWORD/EMAIL -- that assumption in README.md and
# in this script's own xloader-token step below was never true, it just
# never got exercised by anything that logs in (CI only hits the Action
# API, never the login form). Create it here, the same place that
# already depends on it existing.
#
# Two commands, not one: `ckan sysadmin add` only *promotes* an existing
# user -- it aborts with "User not found" for a missing one regardless
# of `-y`, it does not create one inline. `ckan user add` is the command
# that actually creates the account. Neither call's exit code reliably
# signals "already exists"/"already sysadmin" in this CKAN version (a
# `user show` pre-check here previously looked like it worked, but its
# own exit code was equally unreliable and the guarded block never ran)
# so just attempt both, unconditionally, and tolerate either erroring
# because the desired end state already holds.
CKAN_SYSADMIN_NAME="${CKAN_SYSADMIN_NAME:-ckan_admin}"
echo "[worker] ensuring user ${CKAN_SYSADMIN_NAME} exists"
"${CKAN_CLI[@]}" user add "${CKAN_SYSADMIN_NAME}" \
    email="${CKAN_SYSADMIN_EMAIL:-admin@example.com}" \
    password="${CKAN_SYSADMIN_PASSWORD:?CKAN_SYSADMIN_PASSWORD must be set}" \
    || echo "[worker] 'user add' errored (likely already exists) -- continuing"
echo "[worker] ensuring ${CKAN_SYSADMIN_NAME} has sysadmin rights"
"${CKAN_CLI[@]}" sysadmin add "${CKAN_SYSADMIN_NAME}" \
    || echo "[worker] 'sysadmin add' errored (likely already sysadmin) -- continuing"

# docker-entrypoint-initdb.d/20_create_datastore.sh only creates the
# datastore_ro role and the datastore database -- CKAN's own install
# docs are explicit that `ckan datastore set-permissions` still has to
# run against that database afterwards (it grants the actual table
# privileges and creates the `_table_metadata` view/trigger functions
# ckanext-datastore's own action layer depends on, e.g. datastore_info,
# which ckanext-xloader's after_resource_update hook calls on every
# resource update). Nothing here ever ran it before, so datastore has
# never actually been through a real write until something finally
# exercised it end to end. The generated SQL uses CREATE OR REPLACE and
# GRANT, so re-running it on a restart is a safe no-op.
echo "[worker] applying ckanext-datastore permissions/_table_metadata"
"${CKAN_CLI[@]}" datastore set-permissions | \
    PGPASSWORD="${POSTGRES_PASSWORD}" psql -v ON_ERROR_STOP=1 \
        -h db -U "${POSTGRES_USER}" -d "${DATASTORE_DB}"

# ckanext-xloader's worker needs its own API token to call back into the
# CKAN action API while a load job runs (see ckanext-xloader README,
# "Installation" step 5). The web and worker containers each build their
# own ckan.ini from the same image, so this can't just be copied from one
# to the other -- generate one locally, once, and record it in this
# container's own config so restarts don't keep minting new tokens.
if [[ "${CKAN__PLUGINS:-}" == *"xloader"* ]]; then
    if ! grep -q "^ckanext.xloader.api_token" "${CKAN_INI}" 2>/dev/null; then
        echo "[worker] provisioning ckanext.xloader.api_token for the worker process"
        XLOADER_TOKEN=$("${CKAN_CLI[@]}" user token add "${CKAN_SYSADMIN_NAME:-ckan_admin}" xloader-worker | tail -n 1 | tr -d '\t')
        ckan config-tool "${CKAN_INI}" "ckanext.xloader.api_token=${XLOADER_TOKEN}"
    fi
fi

pids=()

echo "[worker] starting: ckan harvester gather-consumer"
"${CKAN_CLI[@]}" harvester gather-consumer &
pids+=("$!")

echo "[worker] starting: ckan harvester fetch-consumer"
"${CKAN_CLI[@]}" harvester fetch-consumer &
pids+=("$!")

echo "[worker] starting: ckan jobs worker (xloader + core background jobs)"
"${CKAN_CLI[@]}" jobs worker &
pids+=("$!")

echo "[worker] starting: harvester run-scheduler loop (every ${HARVEST_RUN_INTERVAL_SECONDS}s)"
(
    while true; do
        sleep "${HARVEST_RUN_INTERVAL_SECONDS}"
        "${CKAN_CLI[@]}" harvester run || echo "[worker] 'ckan harvester run' failed" >&2
    done
) &
pids+=("$!")

echo "[worker] all processes started (pids: ${pids[*]}); waiting..."
wait -n "${pids[@]}"
echo "[worker] a supervised process exited -- exiting so the container restarts" >&2
exit 1
