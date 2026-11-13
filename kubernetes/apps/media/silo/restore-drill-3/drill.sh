#!/bin/bash
# Start Silo on the restored database, copied state and preserved SECRET_KEY,
# entirely inside this pod. Only a fixed summary line reaches the logs.
set -euo pipefail
umask 077
mkdir -m 0700 /tmp/private
exec 3>&1 > /tmp/private/drill.log 2>&1
say() { printf '%s\n' "$1" >&3; }
silo_pid=
finish() {
  result=$?
  trap - EXIT
  if [ -n "$silo_pid" ] && kill -0 "$silo_pid" 2>/dev/null; then
    kill -TERM "$silo_pid" 2>/dev/null || true
    for _ in $(seq 1 90); do kill -0 "$silo_pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$silo_pid" 2>/dev/null; then
      kill -KILL "$silo_pid" 2>/dev/null || true
      [ "$result" -ne 0 ] || result=1
    fi
  fi
  rm -f /tmp/private/ciphertexts.tsv
  if [ "$result" -ne 0 ]; then
    say "silo-restore-drill-3: failed at stage=$stage"
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM
stage=identity
[ "$(id -u)" -eq 3000 ]
unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE PGOPTIONS
export PGPASSFILE=/dev/null PGSERVICEFILE=/dev/null PGCONNECT_TIMEOUT=10
conn='host=127.0.0.1 port=5432 user=silo_drill dbname=silo_restore_drill sslmode=disable'
q() { psql --dbname="$conn" -X --no-password -At -v ON_ERROR_STOP=1 -c "$1"; }
tab=$'\t'
qt() { psql --dbname="$conn" -X --no-password -At -F "$tab" -v ON_ERROR_STOP=1 -c "$1"; }

stage=dependencies
for _ in $(seq 1 120); do pg_isready --dbname="$conn" -q && break; sleep 1; done
pg_isready --dbname="$conn" -q
for _ in $(seq 1 60); do (exec 9<>/dev/tcp/127.0.0.1/6379) 2>/dev/null && break; sleep 1; done
(exec 9<>/dev/tcp/127.0.0.1/6379)
[ -d /config/silo/plugins ]

stage=catalog
users=$(q "SELECT count(*) FROM users;")
plugins=$(q "SELECT coalesce(string_agg(plugin_id || '@' || version || CASE WHEN enabled THEN '' ELSE '(disabled)' END, ',' ORDER BY id), '') FROM plugin_installations;")
oidc_installs=$(q "SELECT count(*) FROM plugin_installations WHERE plugin_id = 'silo.auth.oidc' AND enabled;")
auth_bindings=$(q "SELECT count(*) FROM plugin_auth_bindings WHERE enabled;")
identities=$(q "SELECT count(*) FROM plugin_auth_identities;")
libraries=$(q "SELECT count(*) FROM media_folders;")
schema_sql="SELECT CASE WHEN to_regclass('public.goose_db_version') IS NULL THEN 'n/a' ELSE (SELECT max(version_id)::text FROM goose_db_version WHERE is_applied) END;"
schema_before=$(q "$schema_sql")
[ "$users" -gt 0 ]
[ "$oidc_installs" -eq 1 ]
[ "$auth_bindings" -gt 0 ]
[ "$identities" -gt 0 ]

stage=key
# Ciphertexts stay in this private scratch file; the checker prints counts only.
{
  qt "SELECT 'plugin', 'plugin_runtime_configs:config_value:' || plugin_installation_id || ':' || config_key, config_value->>'__silo_encrypted_runtime_config_v1' FROM plugin_runtime_configs WHERE config_value ? '__silo_encrypted_runtime_config_v1';"
  for target in \
    'subtitle_provider_config|api_key|provider_name' \
    'subtitle_provider_config|password|provider_name' \
    "watch_provider_connections|access_token|provider || ':' || user_id::text || ':' || profile_id" \
    "watch_provider_connections|refresh_token|provider || ':' || user_id::text || ':' || profile_id" \
    'webhook_sync_connections|access_token|id::text' \
    'history_import_sources|admin_token|id::text' \
    'history_import_connect_sessions|connect_access_token|id::text' \
    'history_import_plex_sessions|auth_token|id::text' \
    'jellycompat_sessions|streamapp_access_token|token' \
    'jellycompat_sessions|streamapp_refresh_token|token'; do
    IFS='|' read -r table column aad <<<"$target"
    if [ "$(q "SELECT to_regclass('public.$table') IS NOT NULL;")" = t ]; then
      qt "SELECT 'column', '$table:$column:' || ($aad), $column FROM $table WHERE $column LIKE 'enc:v1:%';"
    fi
  done
} > /tmp/private/ciphertexts.tsv
oidc_configs=$(q "SELECT count(*) FROM plugin_runtime_configs c JOIN plugin_installations i ON i.id = c.plugin_installation_id WHERE i.plugin_id = 'silo.auth.oidc' AND c.config_value ? '__silo_encrypted_runtime_config_v1';")
[ "$oidc_configs" -gt 0 ]
key_result=$(node /scripts/keycheck.js /tmp/private/ciphertexts.tsv)
rm -f /tmp/private/ciphertexts.tsv

stage=startup
started=$(date +%s)
/usr/local/bin/silo > /tmp/private/silo.log 2>&1 &
silo_pid=$!
ready=0
for _ in $(seq 1 1200); do
  kill -0 "$silo_pid"
  if curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/api/v1/ready; then ready=1; break; fi
  sleep 1
done
[ "$ready" -eq 1 ]
ready_seconds=$(( $(date +%s) - started ))

stage=serving
curl -fsS -o /dev/null -m 10 http://127.0.0.1:8080/api/v1/health
curl -fsS -o /dev/null -m 10 http://127.0.0.1:8080/
# Let startup tasks (backfills, plugin load) run before reading the log.
sleep 60
kill -0 "$silo_pid"
curl -fsS -o /dev/null -m 10 http://127.0.0.1:8080/api/v1/ready
schema_after=$(q "$schema_sql")
[ "$schema_after" = "$schema_before" ]
errors=$(grep -c 'level=ERROR' /tmp/private/silo.log || true)
cipher_errors=$(grep -ciE 'decrypt|secret cipher|authenticate ciphertext' /tmp/private/silo.log || true)
[ "$cipher_errors" -eq 0 ]

stage=shutdown
kill -TERM "$silo_pid"
for _ in $(seq 1 90); do kill -0 "$silo_pid" 2>/dev/null || break; sleep 1; done
if kill -0 "$silo_pid" 2>/dev/null; then exit 1; fi
silo_pid=
say "silo-restore-drill-3: passed; users=$users; libraries=$libraries; plugins=$plugins; auth_bindings=$auth_bindings; identities=$identities; schema=$schema_after; key: $key_result; ready_seconds=$ready_seconds; log_errors=$errors; cipher_errors=$cipher_errors"
