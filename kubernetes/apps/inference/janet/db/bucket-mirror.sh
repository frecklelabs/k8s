#!/bin/sh
set -eu

# Barman uploads STARTED metadata before the tarballs, then replaces it with DONE.
# Keep WAL independent of base selection so a long backup cannot delay archiving.
root=$MIRROR_ROOT
work=$(mktemp -d)
source="rgw:$BUCKET_NAME/$SERVER_NAME"
rclone copy "$source/wals" "$root/$SERVER_NAME/wals" \
  --immutable --checksum --fast-list --transfers=8 \
  --stats-one-line --stats=0 --log-level=INFO
rclone check "$source/wals" "$root/$SERVER_NAME/wals" --one-way

rclone lsf "$source/base" --dirs-only > "$work/backups"
completed=0
while IFS= read -r directory; do
  backup_id=$(printf '%s' "$directory" | sed 's:/$::')
  # Never let remote names escape the intended destination tree.
  echo "$backup_id" | grep -Eq '^[0-9]{8}T[0-9]{6}$' || exit 1
  remote="$source/base/$backup_id"
  destination="$root/$SERVER_NAME/base/$backup_id"
  rclone cat "$remote/backup.info" > "$work/backup.info"
  if ! awk -F= '$1 == "status" { count++; done = ($2 == "DONE") }
      END { exit !(count == 1 && done) }' "$work/backup.info"; then
    echo "NAS deferred incomplete base: $backup_id"
    continue
  fi
  # A DONE catalog entry must carry the recovery boundary, not just a status.
  grep -Eq '^begin_wal=[0-9A-F]{24}$' "$work/backup.info"
  grep -Eq '^end_wal=[0-9A-F]{24}$' "$work/backup.info"
  grep -Eq '^end_time=.+$' "$work/backup.info"
  if grep -Eq '^end_time=None$' "$work/backup.info"; then exit 1; fi
  rclone lsf "$remote" --recursive --files-only > "$work/files"
  grep -Eq '^data.*\.tar(\..*)?$' "$work/files"

  # Preserve the original tree, including any prematurely copied backup.info.
  # Its finalized replacement is published separately only after data checks.
  rclone copy "$remote" "$destination" --exclude /backup.info \
    --immutable --checksum --transfers=8 --stats=0 --log-level=INFO
  rclone check "$remote" "$destination" --exclude /backup.info --download --one-way
  rclone cat "$remote/backup.info" > "$work/after.info"
  cmp "$work/backup.info" "$work/after.info"
  digest=$(sha256sum "$work/backup.info" | awk '{print $1}')
  finalized="$root/finalized-metadata-v1/$SERVER_NAME/base/$backup_id/$digest"
  rclone copyto "$work/backup.info" "$finalized/backup.info" --immutable --checksum
  cmp "$work/backup.info" "$finalized/backup.info"
  echo "NAS verified completed base: $backup_id metadata-sha256=$digest"
  completed=$((completed + 1))
done < "$work/backups"

# A green job requires a verified completed base as well as nonempty WAL.
[ "$completed" -gt 0 ]
rclone lsf "$root/$SERVER_NAME/wals" --recursive --files-only > "$work/wals"
[ -s "$work/wals" ]
echo "NAS inventory: verified-completed=$completed WAL=$(wc -l < "$work/wals") files"
