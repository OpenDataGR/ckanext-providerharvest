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
CKAN_WEB_URL="${CKAN_SITE_URL:-http://ckan:5000}"
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
# API, never the login form). Create it here, idempotently, the same
# place that already depends on it existing.
CKAN_SYSADMIN_NAME="${CKAN_SYSADMIN_NAME:-ckan_admin}"
if ! "${CKAN_CLI[@]}" user show "${CKAN_SYSADMIN_NAME}" >/dev/null 2>&1; then
    echo "[worker] creating sysadmin user ${CKAN_SYSADMIN_NAME}"
    "${CKAN_CLI[@]}" sysadmin add "${CKAN_SYSADMIN_NAME}" \
        email="${CKAN_SYSADMIN_EMAIL:-admin@example.com}" \
        password="${CKAN_SYSADMIN_PASSWORD:?CKAN_SYSADMIN_PASSWORD must be set}" \
        -y
fi

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
