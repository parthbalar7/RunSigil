#!/bin/sh
set -eu

psql --set ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set app_password="$RUNSIGIL_POSTGRES_APP_PASSWORD" \
  --set worker_password="$RUNSIGIL_POSTGRES_WORKER_PASSWORD" \
  --set gateway_auth_password="$RUNSIGIL_POSTGRES_GATEWAY_AUTH_PASSWORD" <<-'SQL'
SELECT format('CREATE ROLE runsigil_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'runsigil_app') \gexec
SELECT format('CREATE ROLE runsigil_worker LOGIN PASSWORD %L', :'worker_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'runsigil_worker') \gexec
SELECT format('CREATE ROLE runsigil_gateway_authorizer LOGIN PASSWORD %L', :'gateway_auth_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'runsigil_gateway_authorizer') \gexec
ALTER ROLE runsigil_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE runsigil_worker NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE runsigil_gateway_authorizer NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
