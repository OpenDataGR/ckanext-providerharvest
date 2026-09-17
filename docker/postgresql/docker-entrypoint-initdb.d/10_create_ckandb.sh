#!/bin/bash
# Runs once, the first time the postgres data volume is initialized.
# Creates the role + database CKAN itself uses for all non-DataStore data.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE ROLE "$CKAN_DB_USER" NOSUPERUSER CREATEDB CREATEROLE LOGIN PASSWORD '$CKAN_DB_PASSWORD';
    CREATE DATABASE "$CKAN_DB" OWNER "$CKAN_DB_USER" ENCODING 'utf-8';
EOSQL
