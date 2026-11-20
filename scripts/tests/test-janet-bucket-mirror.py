"""Run the deployed shell against a filesystem-backed rclone test double."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'kubernetes/apps/inference/janet/db/bucket-mirror.sh'
INFO = ('status=DONE\nbegin_wal=000000010000000000000001\n'
        'end_wal=000000010000000000000002\nend_time=2026-11-20 12:00:05+00:00\n')
FAKE = r'''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
cmd = args[0]
def path(s):
    return pathlib.Path(os.environ['SOURCE']) / s[len('rgw:bucket/'):] if s.startswith('rgw:bucket/') else pathlib.Path(s)
a = path(args[1])
if cmd == 'cat':
    sys.stdout.buffer.write(a.read_bytes())
elif cmd == 'lsf':
    if '--dirs-only' in args:
        for p in sorted(a.iterdir()):
            if p.is_dir(): print(p.name + '/')
    else:
        for p in sorted(a.rglob('*')):
            if p.is_file(): print(p.relative_to(a))
elif cmd in ('copy', 'copyto', 'check'):
    b = path(args[2])
    if cmd != 'check':
        assert '--immutable' in args and '--checksum' in args
    elif a.name != 'wals':
        assert '--download' in args
    pairs = [(a, b)] if cmd == 'copyto' else [(p, b / p.relative_to(a)) for p in a.rglob('*') if p.is_file()]
    for src, dst in pairs:
        if '--exclude' in args and src.relative_to(a).as_posix() == 'backup.info': continue
        if dst.exists():
            if dst.read_bytes() != src.read_bytes(): sys.exit(3)
        elif cmd == 'check': sys.exit(4)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    if cmd == 'check' and a.name != 'wals' and os.environ.get('UNSTABLE'):
        (a / 'backup.info').write_text((a / 'backup.info').read_text() + 'changed=yes\n')
else: sys.exit(2)
'''


class Mirror(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.mirror = self.root / 'mirror'
        self.base = self.source / 'server/base/20261120T120000'
        self.base.mkdir(parents=True)
        (self.base / 'backup.info').write_text(INFO)
        (self.base / 'data.tar.bz2').write_bytes(b'backup bytes')
        wal = self.source / 'server/wals/00000001/segment.bz2'
        wal.parent.mkdir(parents=True)
        wal.write_bytes(b'wal bytes')
        (self.root / 'rclone').write_text(FAKE)
        (self.root / 'rclone').chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                        SOURCE=str(self.source), MIRROR_ROOT=str(self.mirror),
                        BUCKET_NAME='bucket', SERVER_NAME='server', TMPDIR=str(self.root))

    def run_mirror(self, success=True):
        before = {p: p.read_bytes() for p in self.mirror.rglob('*') if p.is_file()}
        result = subprocess.run(['/bin/sh', str(SCRIPT)], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data, f'Existing file changed: {p}')
        return result

    def metadata(self):
        return list(self.mirror.glob('finalized-metadata-v1/**/backup.info'))

    def test_conflicting_original_preserved_and_idempotent(self):
        old = self.mirror / 'server/base/20261120T120000/backup.info'
        old.parent.mkdir(parents=True)
        old.write_text('status=STARTED\n')
        self.run_mirror()
        self.run_mirror()
        finalized, = self.metadata()
        self.assertEqual(finalized.read_text(), INFO)
        self.assertEqual(finalized.parent.name, hashlib.sha256(INFO.encode()).hexdigest())
        self.assertEqual((old.parent / 'data.tar.bz2').read_bytes(), b'backup bytes')

    def test_long_running_backup_is_deferred_but_wal_continues(self):
        running = self.source / 'server/base/20261120T000000'
        running.mkdir()
        (running / 'backup.info').write_text('status=STARTED\n')
        result = self.run_mirror()
        self.assertIn('NAS deferred incomplete base: 20261120T000000', result.stdout)
        self.assertFalse((self.mirror / 'server/base/20261120T000000').exists())
        self.assertTrue((self.mirror / 'server/wals/00000001/segment.bz2').exists())
        (running / 'backup.info').write_text(INFO)
        (running / 'data.tar.bz2').write_bytes(b'long backup')
        self.run_mirror()
        self.assertEqual(len(self.metadata()), 2)

    def test_unstable_metadata_is_not_published(self):
        self.env['UNSTABLE'] = '1'
        self.run_mirror(False)
        self.assertEqual(self.metadata(), [])

    def test_same_size_data_conflict_fails_without_publishing(self):
        data = self.mirror / 'server/base/20261120T120000/data.tar.bz2'
        data.parent.mkdir(parents=True)
        data.write_bytes(b'broken bytes')
        self.run_mirror(False)
        self.assertEqual(self.metadata(), [])

    def test_wal_conflict_fails_without_replacement(self):
        wal = self.mirror / 'server/wals/00000001/segment.bz2'
        wal.parent.mkdir(parents=True)
        wal.write_bytes(b'bad bytes')
        self.run_mirror(False)

    def test_incomplete_or_malformed_metadata_never_published(self):
        for info in ('status=STARTED\n', 'status=FAILED\n', 'status=DONE\n',
                     INFO.replace('end_time=2026-11-20 12:00:05+00:00', 'end_time=None'),
                     INFO + 'status=STARTED\n'):
            with self.subTest(info=info):
                (self.base / 'backup.info').write_text(info)
                self.run_mirror(False)
                self.assertEqual(self.metadata(), [])

    def test_empty_data_cannot_publish_metadata(self):
        (self.base / 'data.tar.bz2').unlink()
        self.run_mirror(False)
        self.assertEqual(self.metadata(), [])

    def test_changed_final_metadata_gets_another_version(self):
        self.run_mirror()
        (self.base / 'backup.info').write_text(INFO + 'note=amended\n')
        self.run_mirror()
        self.assertEqual(len(self.metadata()), 2)


if __name__ == '__main__':
    unittest.main()
