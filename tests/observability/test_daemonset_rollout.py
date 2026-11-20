#!/usr/bin/env python3
"""Exercise the pilot exception in the deployed values with promtool/PyYAML."""
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / 'kubernetes/apps/observability/victoria-metrics/k8s-stack/helm/values.yaml'
RULE = 'KubeDaemonSetRolloutStuck'
expr = yaml.safe_load(VALUES.read_text())['defaultRules']['rules'][RULE]['spec']['expr']


def scenario(name, *, current=10, available=10, updated=0, misscheduled=0,
             cluster='fairy-k8s01', namespace='observability',
             daemonset='vm-k8s-stack-prometheus-node-exporter',
             alert=False, progressing=False):
    labels = dict(cluster=cluster, namespace=namespace, daemonset=daemonset,
                  job='kube-state-metrics')
    labels = {key: value for key, value in labels.items() if value}
    selector = ','.join(f'{key}="{value}"' for key, value in labels.items())
    counts = dict(current_number_scheduled=current, desired_number_scheduled=10,
                  number_available=available, updated_number_scheduled=updated,
                  number_misscheduled=misscheduled)
    series = [
        {'series': f'kube_daemonset_status_{metric}{{{selector}}}',
         'values': ('0+0x17 1+0x3' if progressing and metric == 'updated_number_scheduled'
                    else f'{value}+0x30')}
        for metric, value in counts.items()
    ]
    return {'name': name, 'interval': '1m', 'input_series': series,
            'alert_rule_test': [{'eval_time': '20m', 'alertname': RULE,
                                 'exp_alerts': [{'exp_labels': {**labels, 'severity': 'warning'},
                                                 'exp_annotations': {}}] if alert else []}]}


cases = [
    scenario('healthy staged pilot'),
    scenario('partly updated healthy pilot', updated=1),
    scenario('fully updated healthy pilot', updated=10),
    scenario('unavailable staged pilot', available=9, alert=True),
    scenario('misscheduled staged pilot', misscheduled=1, alert=True),
    scenario('unscheduled staged pilot', current=9, alert=True),
    scenario('fully updated unavailable pilot', updated=10, available=9, alert=True),
    scenario('fully updated misscheduled pilot', updated=10, misscheduled=1, alert=True),
    scenario('fully updated unscheduled pilot', updated=10, current=9, alert=True),
    scenario('other cluster retains rollout check', cluster='atlantis-k8s01', alert=True),
    scenario('other namespace retains rollout check', namespace='other', alert=True),
    scenario('other daemonset retains rollout check', daemonset='other', alert=True),
    scenario('missing cluster label retains rollout check', cluster='', alert=True),
    scenario('ordinary healthy rollout', daemonset='other', updated=10),
    scenario('ordinary rollout still progressing', daemonset='other', progressing=True),
]
with tempfile.TemporaryDirectory(prefix='daemonset-rules-') as directory:
    path = Path(directory)
    (path / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'test', 'rules': [
        {'alert': RULE, 'expr': expr, 'for': '15m', 'labels': {'severity': 'warning'}}]}]}))
    (path / 'tests.yaml').write_text(yaml.safe_dump({'rule_files': ['rules.yaml'],
                                                  'evaluation_interval': '1m', 'tests': cases}))
    subprocess.run([shutil.which('promtool') or 'promtool', 'test', 'rules', 'tests.yaml'],
                   cwd=path, check=True)
print(f'{len(cases)} DaemonSet rollout scenarios passed')
