#!/usr/bin/env python3
"""Exercise measurement gates and log redaction without a database or cluster."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'kubernetes/apps/media/silo/db-verify-export2/verify.sh'


class ArchiveVerifier(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'archive'
        self.scratch = self.root / 'scratch'
        self.bin = self.root / 'bin'
        for directory in (self.archive, self.scratch, self.bin):
            directory.mkdir()
        self.dump = b'synthetic archive fixture\n'
        self.digest = hashlib.sha256(self.dump).hexdigest()
        (self.archive / 'silo.dump').write_bytes(self.dump)
        (self.archive / 'silo.dump.sha256').write_text(self.digest + '  silo.dump\n')
        (self.archive / 'recovery-point.txt').write_text('c1942-pre-cutover-2\n')
        (self.archive / 'export-complete.txt').write_text('export complete\n')
        self.command('id', 'echo 26')
        self.command('pg_restore', '''[ "$1" = --list ] || exit 1
echo 'PRIVATE_DIAGNOSTIC' >&2
printf '; private header\n1; private table\n2; private index\n\n'
''')
        script = SCRIPT.read_text().replace('/archive/', str(self.archive) + '/')
        script = script.replace('/scratch/', str(self.scratch) + '/')
        self.script = self.root / 'verify.sh'
        self.script.write_text(script)

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def run_verifier(self):
        before = {p.name: p.read_bytes() for p in self.archive.iterdir()}
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(['sh', str(self.script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.stderr, '')
        self.assertNotIn('PRIVATE', result.stdout)
        self.assertNotIn('private', result.stdout)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.archive.iterdir()})
        return result

    def test_success_exact_output(self):
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, f'dump_bytes={len(self.dump)}\nsha256={self.digest}\n'
                         'checksum_match=true\nrecovery_point=c1942-pre-cutover-2\n'
                         'completion_present=true\npg_restore_entries=2\n')

    def test_checksum_mismatch(self):
        (self.archive / 'silo.dump.sha256').write_text('0' * 64 + '  silo.dump\n')
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum_match=false\n', result.stdout)

    def test_completion_missing(self):
        (self.archive / 'export-complete.txt').unlink()
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('completion_present=false\n', result.stdout)

    def test_marker_redacted(self):
        (self.archive / 'recovery-point.txt').write_text('PRIVATE_WRONG_MARKER\n')
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_list_failure_redacted(self):
        self.command('pg_restore', 'echo PRIVATE_DIAGNOSTIC >&2; exit 1')
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_empty_archive_rejected(self):
        (self.archive / 'silo.dump').write_bytes(b'')
        self.assertNotEqual(self.run_verifier().returncode, 0)

    def test_empty_toc_rejected(self):
        self.command('pg_restore', 'echo "; private header"')
        result = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pg_restore_entries=0\n', result.stdout)


if __name__ == '__main__':
    unittest.main()
