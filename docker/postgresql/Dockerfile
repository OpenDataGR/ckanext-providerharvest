# Postgres image for the CKAN + DataStore replica.
#
# Adds two init scripts (run once, only against a fresh/empty data
# directory) that create:
#   - the CKAN application database + its owning role
#   - the DataStore database, plus a NOCREATEDB/NOCREATEROLE read-only
#     role that ckanext-datastore's query API (datastore_search /
#     datastore_search_sql) connects as -- never the same role CKAN
#     itself writes through.
#
# See https://docs.ckan.org/en/2.11/maintaining/datastore.html#setting-permissions
# for why the read-only role matters: without it, datastore_search_sql
# would run arbitrary read queries under the same role that owns/writes
# the data, which is the whole DataStore security model's write/read
# split.
FROM postgres:16-alpine

COPY docker-entrypoint-initdb.d /docker-entrypoint-initdb.d
# Set explicitly at build time rather than relying on git/checkout to
# preserve the executable bit (a Windows checkout of this repo won't).
RUN chmod +x /docker-entrypoint-initdb.d/*.sh
