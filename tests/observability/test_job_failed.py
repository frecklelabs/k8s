#!/usr/bin/env python3
"""Exercise the shared failed Job rule with vmalert-tool (requires PyYAML)."""
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / 'kubernetes/apps/observability/victoria-metrics/k8s-stack/helm/values.yaml'
NAME = 'KubeJobFailed'
SPEC = yaml.safe_load(VALUES.read_text())['defaultRules']['rules'][NAME]['spec']
LABELS = dict(cluster='test', namespace='test', job='kube-state-metrics')


def metric(name, value, **labels):
    return dict(series=name + '{' + ','.join(f'{key}="{item}"' for key, item in labels.items()) + '}',
                values=f'{value}+0x20')


def case(name, *, owners=('backup',), successes=(), failed=1, alert=True,
         controller='true', expected_owner=None):
    inputs = []
    for instance in ('old-exporter', 'new-exporter'):
        job = LABELS | dict(job_name='backup-job', instance=instance)
        inputs.extend([
            metric('kube_job_failed', failed, **job, reason='BackoffLimitExceeded'),
            metric('kube_job_status_start_time', 100, **job),
            *(metric('kube_job_owner', 1, **job, owner_kind='CronJob', owner_name=owner,
                     owner_is_controller=controller)
              for owner in owners),
            *(metric('kube_cronjob_status_last_successful_time', value,
                     **(LABELS | dict(instance=instance, cronjob=owner) | identity))
              for owner, value, identity in successes),
        ])
    expected_labels = LABELS | dict(job_name='backup-job', reason='BackoffLimitExceeded',
                                    severity='warning')
    if expected_owner is not None:
        expected_labels['cronjob_owner'] = expected_owner
    expected = dict(exp_labels=expected_labels, exp_annotations={})
    return dict(name=name, interval='1m', input_series=inputs,
                alert_rule_test=[dict(eval_time=f'{minute}m', alertname=NAME,
                                      exp_alerts=[expected] if alert and minute >= 15 else [])
                                 for minute in (14, 15, 20)])


cases = [
    case('plain failed Job', owners=()),
    case('failed Job with no successful CronJob run', expected_owner='backup'),
    case('same CronJob recovered', successes=[('backup', 101, {})], alert=False),
    case('same CronJob success before start', successes=[('backup', 99, {})], expected_owner='backup'),
    case('same CronJob success at start', successes=[('backup', 100, {})], expected_owner='backup'),
    case('unrelated CronJob success', successes=[('other', 101, {})], expected_owner='backup'),
    case('same CronJob name in another namespace or cluster',
         successes=[('backup', 101, dict(namespace='other')), ('backup', 101, dict(cluster='other'))],
         expected_owner='backup'),
    case('ambiguous controllers leave the alert unlabeled', owners=('backup', 'other')),
    case('non-controller leaves the alert unlabeled', controller='false'),
    case('distinct owner names retained', owners=('backup', 'other'),
         successes=[('other', 101, {})], alert=False),
    case('healthy Job', failed=0, alert=False),
]
with tempfile.TemporaryDirectory(prefix='job-failed-') as directory:
    path = Path(directory)
    rule = dict(alert=NAME, **SPEC, labels=dict(severity='warning')) | {'for': SPEC.get('for', '15m')}
    (path / 'rules.yaml').write_text(yaml.safe_dump(dict(groups=[dict(name='test', rules=[rule])])))
    (path / 'tests.yaml').write_text(yaml.safe_dump(dict(rule_files=['rules.yaml'],
        evaluation_interval='1m', tests=cases)))
    subprocess.run([sys.argv[1] if len(sys.argv) > 1 else 'vmalert-tool',
                    'unittest', '-files', str(path / 'tests.yaml'), '-disableAlertgroupLabel'],
                   cwd=path, check=True)
print(f'{len(cases)} failed Job scenarios passed')
