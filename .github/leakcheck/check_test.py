"""Synthetic API/process fixtures. Failure messages never include fixture values."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("check", Path(__file__).with_name("check.py"))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.buffer = io.BytesIO()


class BackstopTest(unittest.TestCase):
    def exercise(self, change=False, scan_code=0, api_error=False, build_error=False,
                 kind="pull_request_target", group=None):
        marker = "synthetic-forbidden-marker"
        pr = {"head": {"sha": "a" * 40}, "base": {"sha": "b" * 40},
              "title": "safe", "body": None}
        statuses, commands, reads = [], [], []

        def api(request, timeout):
            if request.data:
                statuses.append(json.loads(request.data)["state"])
                data = {}
            else:
                reads.append(request.full_url)
                data = copy.deepcopy(pr)
                if len(reads) == 2 and change:
                    data["body"] = marker
                if api_error:
                    raise ValueError(marker)
            return contextlib.closing(io.BytesIO(json.dumps(data).encode()))

        def command(args, **kwargs):
            commands.append((args, kwargs))
            if args[0] == "go" and build_error:
                raise ValueError(marker)
            if "leakcheck" in args[0]:
                return subprocess.CompletedProcess(args, scan_code,
                    b"rule fixture in PR body line 0\n" if scan_code else b"", marker.encode())
            return subprocess.CompletedProcess(args, 0)

        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / "event.json"
            if kind == "merge_group":
                event.write_text(json.dumps({"merge_group": group or {
                    "head_sha": "c" * 40, "base_sha": "d" * 40,
                    "base_ref": "refs/heads/main"}}))
            else:
                event.write_text('{"number": 42}')
            env = {"GITHUB_TOKEN": marker, "NEVER_LEAK_PATTERNS": marker,
                   "GITHUB_EVENT_NAME": kind,
                   "GITHUB_REPOSITORY": check.REPO, "GITHUB_EVENT_PATH": str(event),
                   "RUNNER_TEMP": tmp, "PATH": os.environ["PATH"]}
            output = Output()
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(check.urllib.request, "urlopen", api), \
                    patch.object(check.subprocess, "run", command), \
                    contextlib.redirect_stdout(output):
                result = check.main()
        if marker in output.getvalue() or marker.encode() in output.buffer.getvalue():
            self.fail("entrypoint echoed fixture")
        for args, kwargs in commands:
            if marker in " ".join(args):
                self.fail("command argument exposes fixture")
            if args[0] in ("go", "git") and marker in str(kwargs["env"]):
                self.fail("build or fetch inherited credential")
        self.last = (statuses, reads, commands)
        return result, statuses

    def test_success(self):
        result, statuses = self.exercise()
        self.assertTrue(result == 0 and statuses == ["pending", "success"], "status lifecycle")

    def test_fail_closed(self):
        for options in ({"change": True}, {"scan_code": 1}, {"scan_code": 2},
                        {"scan_code": -9}, {"build_error": True}):
            result, statuses = self.exercise(**options)
            self.assertTrue(result == 1 and statuses == ["pending", "failure"], "failure lifecycle")

    def test_api_error_no_echo(self):
        result, statuses = self.exercise(api_error=True)
        self.assertTrue(result == 1 and statuses == [], "API error must fail")

    def test_merge_group_success(self):
        result, statuses = self.exercise(kind="merge_group")
        _, reads, commands = self.last
        self.assertTrue(result == 0 and statuses == ["pending", "success"], "group lifecycle")
        self.assertTrue(reads == [], "group scan reads no PR")
        scan = [args for args, _ in commands if "leakcheck" in args[0]]
        self.assertTrue(scan and scan[0][2:4] == ["d" * 40, "c" * 40], "group range")

    def test_merge_group_fail_closed(self):
        for options in ({"scan_code": 1}, {"scan_code": 2}, {"scan_code": -9},
                        {"build_error": True}):
            result, statuses = self.exercise(kind="merge_group", **options)
            self.assertTrue(result == 1 and statuses == ["pending", "failure"], "group failure")

    def test_merge_group_rejects_bad_event(self):
        good = {"head_sha": "c" * 40, "base_sha": "d" * 40, "base_ref": "refs/heads/main"}
        for change in ({"base_ref": "refs/heads/other"}, {"head_sha": "c" * 39},
                       {"base_sha": None}, {"head_sha": 7}):
            result, statuses = self.exercise(kind="merge_group", group={**good, **change})
            self.assertTrue(result == 1 and statuses == [], "bad group must fail without status")

    def test_unknown_event(self):
        result, statuses = self.exercise(kind="push")
        self.assertTrue(result == 1 and statuses == [], "unknown event must fail")


if __name__ == "__main__":
    unittest.main()
