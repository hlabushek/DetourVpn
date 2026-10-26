#!/bin/sh
# First initialization of a new beta volume only. No password is logged or passed in SQL args.
set -eu
export PGPASSWORD="$(cat /run/secrets/pg_bootstrap)"
export VPN_BETA_MIGRATOR_PASSWORD="$(cat /run/secrets/pg_migrator)"
export VPN_BETA_RUNTIME_PASSWORD="$(cat /run/secrets/pg_runtime)"
psql --no-psqlrc --set=ON_ERROR_STOP=1 --username=vpn_beta_bootstrap --dbname=postgres <<'SQL'
\getenv migrator_password VPN_BETA_MIGRATOR_PASSWORD
\getenv runtime_password VPN_BETA_RUNTIME_PASSWORD
CREATE ROLE vpn_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'migrator_password';
CREATE ROLE vpn_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'runtime_password';
CREATE DATABASE detourvpn_beta OWNER vpn_migrator;
REVOKE ALL ON DATABASE detourvpn_beta FROM PUBLIC;
GRANT CONNECT ON DATABASE detourvpn_beta TO vpn_api;
\connect detourvpn_beta
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO vpn_migrator;
GRANT USAGE ON SCHEMA public TO vpn_api;
ALTER DEFAULT PRIVILEGES FOR ROLE vpn_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vpn_api;
ALTER DEFAULT PRIVILEGES FOR ROLE vpn_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO vpn_api;
SQL
unset VPN_BETA_MIGRATOR_PASSWORD VPN_BETA_RUNTIME_PASSWORD PGPASSWORD
