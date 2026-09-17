# CKAN 2.11.3 replica for ckanext-providerharvest, matching data.gov.gr's
# confirmed extension set (see DESIGN.md "Confirmed: Existing data.gov.gr
# Environment"): harvest, datastore, xloader, scheming_datasets, dcat
# (fluent is a scheming/dcat dependency, not separately confirmed but
# required for the multilingual `title_translated`/`notes_translated`
# fields those schemas use).
#
# Built on the official ckan/ckan-base image (see
# https://github.com/ckan/ckan-docker-base), following the same
# extension-install pattern as the reference ckan/ckan-docker project's
# Dockerfile.dev (pip-install each extension as an editable git checkout,
# then its pinned requirements.txt). All five third-party extensions
# below have their master branch confirmed CKAN-2.10/2.11-compatible as
# of the versions checked while building this replica -- pin to a commit
# SHA here instead of `@master` if reproducing an exact historical build
# matters more than picking up upstream fixes.
FROM ckan/ckan-base:2.11.3-py3.10

# --- ckanext-harvest: scheduling, job/queue infra, harvest source model ---
RUN pip3 install -e 'git+https://github.com/ckan/ckanext-harvest.git@master#egg=ckanext-harvest' && \
    pip3 install -r ${SRC_DIR}/ckanext-harvest/pip-requirements.txt

# --- ckanext-xloader: bulk DataStore loads (COPY-based), replaces datapusher ---
RUN pip3 install -e 'git+https://github.com/ckan/ckanext-xloader.git@master#egg=ckanext-xloader' && \
    pip3 install -r ${SRC_DIR}/ckanext-xloader/requirements.txt && \
    pip3 install -U 'requests[security]'

# --- ckanext-fluent: multilingual fields, required by the scheming/dcat schemas ---
RUN pip3 install -e 'git+https://github.com/ckan/ckanext-fluent.git@master#egg=ckanext-fluent' && \
    pip3 install -r ${SRC_DIR}/ckanext-fluent/requirements.txt

# --- ckanext-scheming: provides the `scheming_datasets` plugin ---
RUN pip3 install -e 'git+https://github.com/ckan/ckanext-scheming.git@master#egg=ckanext-scheming'

# --- ckanext-dcat: DCAT-AP RDF profiles/endpoints ---
RUN pip3 install -e 'git+https://github.com/ckan/ckanext-dcat.git@master#egg=ckanext-dcat' && \
    pip3 install -r ${SRC_DIR}/ckanext-dcat/requirements.txt

# --- ckanext-providerharvest: this extension, built from the repo checkout ---
# Copied in at build time (rather than volume-mounted) so the resulting
# image is self-contained and the whole stack reproduces from a single
# `docker compose build` against a fresh clone, per DESIGN.md's
# "Testing Strategy" bar (applies cleanly to a *fresh* copy from scratch).
# Installed with the `[dev]` extra (pytest) too: this image is explicitly
# a disposable local/CI replica, never data.gov.gr's real deployment (see
# the install instructions in README.md for that), so it's fine for it to
# carry test tooling -- it's what lets `docker compose exec ckan pytest
# ckanext/providerharvest/tests` verify the CKAN-coupled layer in place.
COPY --chown=ckan-sys:ckan-sys . ${SRC_DIR}/ckanext-providerharvest
RUN pip3 install -e "${SRC_DIR}/ckanext-providerharvest[dev]"

# Worker entrypoint (harvest gather/fetch consumers + xloader jobs worker +
# a `harvester run` scheduler loop) -- used as the `command:` override for
# the `ckan-worker` service in docker-compose.yml; the `ckan` (web) service
# keeps the base image's default CMD (start_ckan.sh / uwsgi).
COPY --chown=ckan-sys:ckan-sys worker-entrypoint.sh ${APP_DIR}/worker-entrypoint.sh

USER root
RUN chmod +x ${APP_DIR}/worker-entrypoint.sh
USER ckan
