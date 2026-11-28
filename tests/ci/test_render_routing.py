#!/usr/bin/env python3
"""Exercise the workflow's routing shell against synthetic Git commits (PyYAML required)."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/render.yaml').read_text())
ROUTE = next(step['run'] for step in WORKFLOW['jobs']['changes']['steps']
             if step.get('id') == 'files')
GATEWAY_INPUT = Path('kubernetes/apps/networking/gateway-api/input.yaml')


class RenderRouting(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.write(GATEWAY_INPUT)
        self.git('add', '.')
        self.git('commit', '--quiet', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', *args], cwd=cwd or self.repo, text=True).strip()

    def write(self, path):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture\n')

    def merged_commit(self):
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'PR changes')
        head = self.git('rev-parse', 'HEAD')
        tree = self.git('rev-parse', 'HEAD^{tree}')
        merge = self.git('commit-tree', tree, '-p', self.base, '-p', head, '-m', 'PR merge')
        self.git('update-ref', 'refs/heads/fixture-merge', merge)
        checkout = self.root / 'checkout'
        self.git('clone', '--quiet', '--depth=2', '--branch=fixture-merge', self.repo.as_uri(),
                 str(checkout))
        return checkout, merge, head

    def route(self, event, cwd, sha='', base='', head=''):
        output = self.root / 'output'
        output.write_text('')
        env = dict(os.environ, EVENT=event, GITHUB_SHA=sha, BASE_SHA=base, PR_HEAD_SHA=head,
                   GITHUB_OUTPUT=str(output))
        subprocess.run(['bash', '-e', '-c', ROUTE], cwd=cwd, env=env, check=True,
                       capture_output=True, text=True)
        return output.read_text().splitlines()

    def test_docs_only_skips_both(self):
        self.write('docs/README.md')
        checkout, merge, head = self.merged_commit()
        self.assertEqual(self.route('pull_request', checkout, merge, self.base, head),
                         ['render=false', 'gateway_api=false'])

    def assert_root_kustomization_runs_render_only(self, filename):
        self.write(filename)
        checkout, merge, head = self.merged_commit()
        self.assertEqual(self.route('pull_request', checkout, merge, self.base, head),
                         ['render=true', 'gateway_api=false'])

    def test_root_kustomization_yaml_runs_render_only(self):
        self.assert_root_kustomization_runs_render_only('kustomization.yaml')

    def test_root_kustomization_yml_runs_render_only(self):
        self.assert_root_kustomization_runs_render_only('kustomization.yml')

    def test_root_kustomization_uppercase_runs_render_only(self):
        self.assert_root_kustomization_runs_render_only('Kustomization')

    def test_input_beyond_api_file_cap_runs_both(self):
        for index in range(3000):
            self.write(f'docs/{index:04}.md')
        (self.repo / GATEWAY_INPUT).write_text('changed\n')
        checkout, merge, head = self.merged_commit()
        self.assertEqual(len(self.git('diff', '--name-only', self.base, merge,
                                      cwd=checkout).splitlines()), 3001)
        self.assertEqual(self.route('pull_request', checkout, merge, self.base, head),
                         ['render=true', 'gateway_api=true'])

    def test_rename_out_of_input_directory_runs_both(self):
        self.write('docs/README.md')
        (self.repo / GATEWAY_INPUT).rename(self.repo / 'docs/moved.yaml')
        checkout, merge, head = self.merged_commit()
        self.assertEqual(self.route('pull_request', checkout, merge, self.base, head),
                         ['render=true', 'gateway_api=true'])

    def test_unverified_pr_commit_runs_both(self):
        self.write('docs/README.md')
        checkout, merge, head = self.merged_commit()
        self.assertEqual(self.route('pull_request', checkout, merge, head, head),
                         ['render=true', 'gateway_api=true'])

    def test_merge_group_runs_both_without_checkout(self):
        self.assertEqual(self.route('merge_group', self.root), ['render=true', 'gateway_api=true'])

    def test_main_push_only_renders_without_checkout(self):
        self.assertEqual(self.route('push', self.root), ['render=true', 'gateway_api=false'])


if __name__ == '__main__':
    unittest.main()
