#!/bin/sh
# Restore export 3 into a scratch PostgreSQL that lives only in this pod.
# Runtime variables stay unbraced so Flux postBuild leaves them intact.
set -eu
umask 077
fail() { printf '%s\n' 'silo-restore-drill-3: database restore failed; inspect private scratch diagnostics'; exit 1; }
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
[ "$(cat /archive/recovery-point.txt)" = c1942-cutover-3 ] || fail
[ "$(cat /archive/export-complete.txt)" = 'export complete; not restore acceptance' ] || fail
[ ! -e /db/private ] || fail
mkdir -m 0700 /db/private /db/private/socket
# Never inherit a libpq target; every client names its socket target via --dbname.
unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE PGOPTIONS PGCLIENTENCODING
export PGPASSFILE=/dev/null PGSERVICEFILE=/dev/null HOME=/db/private PGCONNECT_TIMEOUT=10
admin_conn='host=/db/private/socket port=5432 user=silo_drill dbname=postgres'
drill_conn='host=/db/private/socket port=5432 user=silo_drill dbname=silo_restore_drill'
server_may_run=0
stop() {
  [ "$server_may_run" -eq 1 ] || return 0
  pg_ctl -D /db/pgdata -m fast -w -t 50 stop >>/db/private/diagnostic.log 2>&1
}
finish() {
  result=$?
  trap - EXIT
  if stop; then
    :
  else
    stop_result=$?
    printf '%s\n' 'silo-restore-drill-3: scratch shutdown failed; inspect private scratch diagnostics'
    [ "$result" -ne 0 ] || result=$stop_result
  fi
  if [ "$result" -ne 0 ]; then
    printf '%s\n' 'silo-restore-drill-3: database restore failed; inspect private scratch diagnostics'
  else
    printf 'silo-restore-drill-3: database restored; public_tables=%s; vector=present; restored_bytes=%s\n' "$tables" "$restored_bytes"
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM
query() {
  psql --dbname="$1" -X --no-password -At -v ON_ERROR_STOP=1 -c "$2"
}
restore() {
  printf '%s  /archive/silo.dump\n' "$EXPECTED_ARCHIVE_SHA256" | sha256sum -c --status
  initdb -D /db/pgdata --username=silo_drill --encoding=UTF8 \
    --locale-provider=libc --locale="$EXPECTED_LOCALE" --auth-local=trust --auth-host=reject
  # The drill application connects over loopback inside this pod only.
  printf '%s\n' 'local all silo_drill trust' 'host silo_restore_drill silo_drill 127.0.0.1/32 trust' > /db/pgdata/pg_hba.conf
  server_may_run=1
  # Socket only while restoring; the sidecar later listens on loopback.
  pg_ctl -D /db/pgdata -l /db/private/postgres.log -w -t 120 \
    -o "-k /db/private/socket -c listen_addresses='' -c unix_socket_permissions=0700 -c max_connections=5 -c dynamic_shared_memory_type=mmap -c max_parallel_maintenance_workers=0" start
  # Export 3 recorded its source identity; a mismatch stops before restore.
  identity=$(query "$admin_conn" \
    "SELECT m->>'database' = 'silo' AND m->>'encoding' = 'UTF8' AND m->>'locale_provider' = 'c' AND m->>'collate' = '$EXPECTED_LOCALE' AND m->>'ctype' = '$EXPECTED_LOCALE' AND (m->>'server_version_num')::int / 10000 = 18 FROM (SELECT pg_read_file('/archive/database-metadata.json')::jsonb AS m) AS s;")
  [ "$identity" = t ]
  query "$admin_conn" \
    "CREATE DATABASE silo_restore_drill TEMPLATE template0 ENCODING 'UTF8' LOCALE_PROVIDER libc LOCALE '$EXPECTED_LOCALE';"
  pg_restore --no-password --exit-on-error --single-transaction --no-owner --no-acl \
    --no-tablespaces --no-subscriptions --no-publications --dbname="$drill_conn" /archive/silo.dump
  target=$(query "$drill_conn" "SELECT current_database();")
  [ "$target" = silo_restore_drill ]
  tables=$(query "$drill_conn" \
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');")
  vector=$(query "$drill_conn" "SELECT count(*) FROM pg_extension WHERE extname = 'vector';")
  restored_bytes=$(query "$drill_conn" "SELECT pg_database_size(current_database());")
  case "$tables" in *[!0-9]*|'') return 1 ;; esac
  case "$restored_bytes" in *[!0-9]*|'') return 1 ;; esac
  [ "$tables" -gt 0 ] || return 1
  [ "$vector" = 1 ] || return 1
}
# PostgreSQL and restore diagnostics can contain row data: keep them in scratch.
restore > /db/private/diagnostic.log 2>&1
