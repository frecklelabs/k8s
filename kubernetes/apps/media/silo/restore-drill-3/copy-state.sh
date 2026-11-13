#!/bin/sh
# Copy the c1942-cutover-3 state backup into scratch; the backup stays read-only.
set -eu
umask 077
fail() { printf '%s\n' 'silo-restore-drill-3: state copy failed'; exit 1; }
[ "$(id -u)" -eq 3000 ] || fail
[ -d /state-backup ] || fail
[ ! -e /state/silo ] || fail
tally() {
  find "$1" -mindepth 1 \( -type f -printf 'f %s\n' -o -type d -printf 'd 0\n' -o -type l -printf 'l 0\n' \) |
    awk '{ n[$1]++; if ($1 == "f") b += $2 } END { printf "%d %d %d %.0f\n", n["f"], n["d"], n["l"], b }'
}
cp -a /state-backup /state/silo 2>/dev/null || fail
source_tally=$(tally /state-backup) || fail
copy_tally=$(tally /state/silo) || fail
[ "$source_tally" = "$copy_tally" ] || fail
set -- $copy_tally
[ "$1" -gt 0 ] || fail
[ -d /state/silo/plugins ] || fail
printf 'silo-restore-drill-3: state copied; files=%s; dirs=%s; symlinks=%s; file_bytes=%s\n' "$1" "$2" "$3" "$4"
