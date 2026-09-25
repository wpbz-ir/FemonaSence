-- Run as a PostgreSQL superuser (for example postgres).
-- Replace the password before executing.
CREATE USER cinema_vault WITH LOGIN PASSWORD 'REPLACE_WITH_A_STRONG_PASSWORD';
CREATE DATABASE cinema_vault OWNER cinema_vault;
