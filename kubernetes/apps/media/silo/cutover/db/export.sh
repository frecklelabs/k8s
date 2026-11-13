#!/bin/sh
# Keep libpq credentials and all database diagnostics out of container logs.
# Flux postBuild rewrites braced variables, so this script uses none.
set -eu
umask 077
exec 3>&1
finish() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ]; then
    printf '%s\n' 'silo-db-export-3: failed; retain partial files and inspect private diagnostics' >&3
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM
say() {
  printf '%s\n' "$1" >&3
}
psql_value() {
  psql --dbname="$DATABASE_URL" -X --no-password -At -v ON_ERROR_STOP=1 -c "$1"
}
export_archive() {
  [ "$APPROVED_RECOVERY_POINT" = c1942-cutover-3 ]
  [ "$(id -u)" -eq 26 ]
  [ -n "$DATABASE_URL" ]
  unset PGDATABASE PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE PGOPTIONS PGCLIENTENCODING
  export PGPASSFILE=/dev/null PGSERVICEFILE=/dev/null HOME=/scratch/private
  export PGAPPNAME=c1942-silo-db-export-3 PGCONNECT_TIMEOUT=15
  export PGOPTIONS='-c default_transaction_read_only=on'
  # The held Silo pod replaced the old one under Recreate; this outlasts its 120s grace.
  sleep 150
  # Writers must be gone: no other session of the application role may remain.
  tries=0
  while :; do
    others=$(psql_value "SELECT count(*) FROM pg_stat_activity WHERE usename = current_user AND pid <> pg_backend_pid();")
    [ "$others" = 0 ] && break
    tries=$((tries + 1))
    [ "$tries" -lt 10 ] || return 2
    sleep 30
  done
  say 'silo-db-export-3: no other application sessions; exporting'
  # Reject any pre-existing contents, including dangling symlinks and hidden files.
  for path in /archive/.[!.]* /archive/..?* /archive/*; do
    if [ -e "$path" ] || [ -L "$path" ]; then return 1; fi
  done
  # Atomic one-attempt claim; never remove this marker or retry this recovery point.
  mkdir -m 0700 /archive/.c1942-export-3
  exec > /archive/.c1942-export-3/diagnostic.log 2>&1
  set -C
  identity=$(psql_value "SELECT current_database() = 'silo' AND current_setting('server_version_num')::int / 10000 = 18 AND pg_encoding_to_char(encoding) = 'UTF8' FROM pg_database WHERE datname = current_database();")
  [ "$identity" = t ]
  psql_value "SELECT json_build_object('database', datname, 'encoding', pg_encoding_to_char(encoding), 'locale_provider', datlocprovider, 'provider_locale', datlocale, 'icu_rules', daticurules, 'collate', datcollate, 'ctype', datctype, 'collation_version', datcollversion, 'server_version_num', current_setting('server_version_num'), 'observed_at', clock_timestamp()) FROM pg_database WHERE datname = current_database();" > /archive/database-metadata.json
  printf '%s\n' "$APPROVED_RECOVERY_POINT" > /archive/recovery-point.txt
  # No filters, row-security override or parallel connections.
  pg_dump --dbname="$DATABASE_URL" --no-password --format=custom --lock-wait-timeout=30s > /archive/silo.dump
  [ -s /archive/silo.dump ]
  (cd /archive && sha256sum silo.dump) > /archive/silo.dump.sha256
  sync
  printf '%s\n' 'export complete; not restore acceptance' > /archive/export-complete.txt
  sync
  # Verify from the archive volume before this Job may complete.
  set +C
  pg_restore --list /archive/silo.dump > /archive/.c1942-export-3/toc.txt
  entries=$(awk '!/^;/ && NF { n++ } END { print n+0 }' /archive/.c1942-export-3/toc.txt)
  bytes=$(stat -c %s /archive/silo.dump)
  sum=$(cut -d ' ' -f 1 /archive/silo.dump.sha256)
  (cd /archive && sha256sum -c --status silo.dump.sha256)
  point=$(cat /archive/recovery-point.txt)
  [ "$point" = "$APPROVED_RECOVERY_POINT" ]
  [ -f /archive/export-complete.txt ]
  [ "$entries" -gt 0 ]
  say "dump_bytes=$bytes"
  say "sha256=$sum"
  say 'checksum_match=true'
  say "recovery_point=$point"
  say 'completion_present=true'
  say "pg_restore_entries=$entries"
  say 'silo-db-export-3: export verified; not restore acceptance'
}
mkdir -m 0700 /scratch/private
export_archive > /scratch/private/diagnostic.log 2>&1
