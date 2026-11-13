#!/bin/sh
# Restore export2 into a throwaway PostgreSQL that exists only in this pod's
# scratch volume. No production credential, Service or network path exists here.
# Runtime variables stay unbraced so Flux postBuild leaves them intact.
set -eu
umask 077
fail() { printf '%s\n' 'silo-db-rehearsal: failed; inspect private scratch diagnostics'; exit 1; }
[ "$(id -u)" -eq 26 ] || fail
case "$EXPECTED_ARCHIVE_SHA256" in
  *[!0-9a-f]*|'') fail ;;
esac
[ "$(printf %s "$EXPECTED_ARCHIVE_SHA256" | wc -c)" -eq 64 ] || fail
# The locale is embedded in SQL below, so only a plain locale name is accepted.
case "$EXPECTED_LOCALE" in
  *[!A-Za-z0-9._-]*|'') fail ;;
esac
for file in silo.dump recovery-point.txt export-complete.txt database-metadata.json; do
  [ -f "/archive/$file" ] && [ ! -L "/archive/$file" ] || fail
done
[ "$(cat /archive/recovery-point.txt)" = c1942-pre-cutover-2 ] || fail
[ "$(cat /archive/export-complete.txt)" = 'export complete; not restore acceptance' ] || fail
[ ! -e /scratch/private ] || fail
mkdir -m 0700 /scratch/private
# Never inherit a libpq target; every client names its socket target via --dbname.
unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE PGOPTIONS PGCLIENTENCODING
export PGPASSFILE=/dev/null PGSERVICEFILE=/dev/null HOME=/scratch/private PGCONNECT_TIMEOUT=10
admin_conn='host=/scratch/private/socket port=5432 user=silo_rehearsal_admin dbname=postgres'
scratch_conn='host=/scratch/private/socket port=5432 user=silo_rehearsal_admin dbname=silo_export2_rehearsal'
server_may_run=0
stop() {
  [ "$server_may_run" -eq 1 ] || return 0
  pg_ctl -D /scratch/private/pgdata -m fast -w -t 50 stop >>/scratch/private/diagnostic.log 2>&1
}
finish() {
  result=$?
  trap - EXIT
  if stop; then
    :
  else
    stop_result=$?
    printf '%s\n' 'silo-db-rehearsal: shutdown failed; inspect private scratch diagnostics'
    # Preserve an earlier restore/startup failure when shutdown also fails.
    [ "$result" -ne 0 ] || result=$stop_result
  fi
  if [ "$result" -ne 0 ]; then
    printf '%s\n' 'silo-db-rehearsal: failed; inspect private scratch diagnostics'
  else
    printf 'silo-db-rehearsal: restore passed; database=silo_export2_rehearsal; public_tables=%s; vector=present; restored_bytes=%s\n' "$tables" "$restored_bytes"
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM
query() {
  psql --dbname="$1" -X --no-password -At -v ON_ERROR_STOP=1 -c "$2"
}
rehearse() {
  printf '%s  /archive/silo.dump\n' "$EXPECTED_ARCHIVE_SHA256" | sha256sum -c --status
  mkdir -m 0700 /scratch/private/socket
  initdb -D /scratch/private/pgdata --username=silo_rehearsal_admin --encoding=UTF8 \
    --locale-provider=libc --locale="$EXPECTED_LOCALE" --auth-local=trust --auth-host=reject
  # A failed or timed-out start can still leave PostgreSQL running.
  server_may_run=1
  # Socket only: no TCP listener; mmap avoids the container's small /dev/shm.
  pg_ctl -D /scratch/private/pgdata -l /scratch/private/postgres.log -w -t 120 \
    -o "-k /scratch/private/socket -c listen_addresses='' -c unix_socket_permissions=0700 -c max_connections=5 -c dynamic_shared_memory_type=mmap -c max_parallel_maintenance_workers=0" start
  # Export2 recorded its source database identity; a mismatch stops before restore.
  identity=$(query "$admin_conn" \
    "SELECT m->>'database' = 'silo' AND m->>'encoding' = 'UTF8' AND m->>'locale_provider' = 'c' AND m->>'collate' = '$EXPECTED_LOCALE' AND m->>'ctype' = '$EXPECTED_LOCALE' AND (m->>'server_version_num')::int / 10000 = 18 FROM (SELECT pg_read_file('/archive/database-metadata.json')::jsonb AS m) AS s;")
  [ "$identity" = t ]
  query "$admin_conn" \
    "CREATE DATABASE silo_export2_rehearsal TEMPLATE template0 ENCODING 'UTF8' LOCALE_PROVIDER libc LOCALE '$EXPECTED_LOCALE';"
  # No --create/--clean: the sole target was created above in this pod's own cluster.
  pg_restore --no-password --exit-on-error --single-transaction --no-owner --no-acl \
    --no-tablespaces --no-subscriptions --no-publications --dbname="$scratch_conn" /archive/silo.dump
  target=$(query "$scratch_conn" "SELECT current_database();")
  [ "$target" = silo_export2_rehearsal ]
  tables=$(query "$scratch_conn" \
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');")
  vector=$(query "$scratch_conn" "SELECT count(*) FROM pg_extension WHERE extname = 'vector';")
  restored_bytes=$(query "$scratch_conn" "SELECT pg_database_size(current_database());")
  case "$tables" in *[!0-9]*|'') return 1 ;; esac
  case "$restored_bytes" in *[!0-9]*|'') return 1 ;; esac
  [ "$tables" -gt 0 ] || return 1
  [ "$vector" = 1 ] || return 1
}
# PostgreSQL and restore diagnostics can contain row data: keep them in scratch.
rehearse > /scratch/private/diagnostic.log 2>&1
