#!/bin/sh
# Keep libpq credentials and all database diagnostics out of container logs.
set -eu
umask 077
exec 3>&1
finish() {
  result=$?
  trap - EXIT
  if [ "$result" -eq 0 ]; then
    printf '%s\n' 'silo-db-export: archive prepared; independent verification required' >&3
  else
    printf '%s\n' 'silo-db-export: failed; retain partial files and inspect private diagnostics' >&3
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM
export_archive() {
  # Keep runtime guards intact through Flux postBuild substitution.
  [ "$APPROVED_RECOVERY_POINT" = c1942-pre-cutover-2 ]
  [ "$(id -u)" -eq 26 ]
  [ -n "$DATABASE_URL" ]
  unset PGDATABASE PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE PGOPTIONS PGCLIENTENCODING
  export PGPASSFILE=/dev/null PGSERVICEFILE=/dev/null HOME=/scratch/private
  export PGAPPNAME=c1942-silo-db-export PGCONNECT_TIMEOUT=15
  export PGOPTIONS='-c default_transaction_read_only=on'
  # Reject any pre-existing contents, including dangling symlinks and hidden files.
  for path in /archive/.[!.]* /archive/..?* /archive/*; do
    if [ -e "$path" ] || [ -L "$path" ]; then return 1; fi
  done
  # Atomic one-attempt claim; never remove this marker or retry this recovery point.
  mkdir -m 0700 /archive/.c1942-export-2
  # Retain diagnostics even after this one-attempt pod exits.
  exec > /archive/.c1942-export-2/diagnostic.log 2>&1
  set -C
  identity=$(psql --dbname="$DATABASE_URL" -X --no-password -At -v ON_ERROR_STOP=1 -c \
    "SELECT current_database() = 'silo' AND current_setting('server_version_num')::int / 10000 = 18 AND pg_encoding_to_char(encoding) = 'UTF8' FROM pg_database WHERE datname = current_database();")
  [ "$identity" = t ]
  # Metadata only, not persisted application settings (pending gate104 is unrelated).
  psql --dbname="$DATABASE_URL" -X --no-password -At -v ON_ERROR_STOP=1 -c \
    "SELECT json_build_object('database', datname, 'encoding', pg_encoding_to_char(encoding), 'locale_provider', datlocprovider, 'provider_locale', datlocale, 'icu_rules', daticurules, 'collate', datcollate, 'ctype', datctype, 'collation_version', datcollversion, 'server_version_num', current_setting('server_version_num'), 'observed_at', clock_timestamp()) FROM pg_database WHERE datname = current_database();" > /archive/database-metadata.json
  printf '%s\n' "$APPROVED_RECOVERY_POINT" > /archive/recovery-point.txt
  # No filters, row-security override or parallel connections.
  pg_dump --dbname="$DATABASE_URL" --no-password --format=custom --lock-wait-timeout=30s > /archive/silo.dump
  [ -s /archive/silo.dump ]
  pg_restore --list /archive/silo.dump > /archive/.c1942-export-2/toc.txt
  (cd /archive && sha256sum silo.dump) > /archive/silo.dump.sha256
  # Flush errors must precede the completion marker; actual NAS durability is a gate.
  sync
  printf '%s\n' 'export complete; not restore acceptance' > /archive/export-complete.txt
  sync
}
mkdir -m 0700 /scratch/private
export_archive > /scratch/private/diagnostic.log 2>&1
