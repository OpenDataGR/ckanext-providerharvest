#!/bin/bash
# Runs once, the first time the postgres data volume is initialized.
#
# ckanext-datastore needs *two* roles against the datastore database:
#   - $CKAN_DB_USER (created in 10_create_ckandb.sh) owns the datastore
#     database and is what CKAN's own `sqlalchemy.url`-style write
#     connection (`ckan.datastore.write_url`) uses -- full DDL/DML.
#   - $DATASTORE_READONLY_USER, created here with NOCREATEDB/NOCREATEROLE
#     and no ownership, is what `ckan.datastore.read_url` uses. CKAN
#     revokes/grants table-level privileges onto this role per-resource
#     as part of `datastore_create`, so it can only ever SELECT, which is
#     what makes exposing datastore_search_sql to anonymous consumers
#     safe to consider at all.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE ROLE "$DATASTORE_READONLY_USER" NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN PASSWORD '$DATASTORE_READONLY_PASSWORD';
    CREATE DATABASE "$DATASTORE_DB" OWNER "$CKAN_DB_USER" ENCODING 'utf-8';
EOSQL
