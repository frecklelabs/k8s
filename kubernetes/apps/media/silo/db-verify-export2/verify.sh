#!/bin/sh
# Archive diagnostics and TOC names must never reach container logs.
set -eu
umask 077
exec 2>/dev/null
verify_archive() {
  [ "$(id -u)" -eq 26 ]
  [ -s /archive/silo.dump ]
  dump_bytes=$(stat -c %s /archive/silo.dump)
  digest_line=$(sha256sum /archive/silo.dump)
  digest=$(printf '%s\n' "$digest_line" | cut -d ' ' -f 1)
  recorded=$(cat /archive/silo.dump.sha256)
  checksum_match=false
  if [ "$recorded" = "$digest  silo.dump" ]; then checksum_match=true; fi
  recovery_point=$(cat /archive/recovery-point.txt)
  # Only the approved marker can be printed; reject arbitrary file contents.
  [ "$recovery_point" = c1942-pre-cutover-2 ]
  completion_present=false
  if [ -f /archive/export-complete.txt ]; then completion_present=true; fi
  pg_restore --list /archive/silo.dump > /scratch/toc.txt
  entry_count=$(awk '!/^;/ && NF { n++ } END { print n+0 }' /scratch/toc.txt)
  printf 'dump_bytes=%s\nsha256=%s\nchecksum_match=%s\nrecovery_point=%s\ncompletion_present=%s\npg_restore_entries=%s\n' \
    "$dump_bytes" "$digest" "$checksum_match" "$recovery_point" "$completion_present" "$entry_count"
  [ "$checksum_match" = true ]
  [ "$completion_present" = true ]
  [ "$entry_count" -gt 0 ]
}
verify_archive
