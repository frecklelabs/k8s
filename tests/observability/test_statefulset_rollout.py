#!/usr/bin/env python3
"""Exercise the deployed values rule with vmalert-tool (requires PyYAML)."""
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / 'kubernetes/apps/observability/victoria-metrics/k8s-stack/helm/values.yaml'
RULE = 'KubeStatefulSetUpdateNotRolledOut'
spec = yaml.safe_load(VALUES.read_text())['defaultRules']['rules'][RULE]['spec']
labels = dict(namespace='test', statefulset='model', job='kube-state-metrics', cluster='test')


def labelset(labels):
    return '{' + ','.join(f'{key}="{value}"' for key, value in labels.items()) + '}'


def scenario(name, *, replicas=2, updated=1, ready=2, partition=1,
             revision='new', alert=False, progressing=False, partition_labels=None,
             duplicate=False, replacement=None):
    series = []

    def exporter(instance, *, update_revision=revision, ready_count=ready,
                 updated_count=updated, rollout_partition=partition, moving=progressing):
        def metric(name, value, *, identity=labels, values=None, **extra):
            metric_labels = identity | dict(instance=instance) | extra
            series.append({'series': name + labelset(metric_labels), 'values': values or f'{value}+0x30'})

        metric('kube_statefulset_status_current_revision', 1, revision='old')
        metric('kube_statefulset_status_update_revision', 1, revision=update_revision)
        metric('kube_statefulset_replicas', replicas)
        metric('kube_statefulset_status_replicas_ready', ready_count)
        metric('kube_statefulset_status_replicas_updated', updated_count,
               values='0+0x17 1+0x12' if moving else None)
        if rollout_partition is not None:
            metric('kube_leaderworkerset_rollout_partition', rollout_partition,
                   identity=partition_labels or labels)

    exporter('old-exporter')
    if duplicate:
        exporter('new-exporter', **(replacement or {}))
    expected = [{'exp_labels': labels | dict(severity='warning'), 'exp_annotations': {}}] if alert else []
    return {'name': name, 'interval': '1m', 'input_series': series,
            'alert_rule_test': [{'eval_time': time, 'alertname': RULE, 'exp_alerts': alerts}
                                for time, alerts in [('14m', []), ('20m', expected)]],
            'metricsql_expr_test': [{'expr': spec['expr'], 'eval_time': '20m',
                                    'exp_samples': [{'labels': labelset(labels), 'value': replicas}]
                                    if alert else []}]}


cases = [
    scenario('fully rolled out', updated=2, partition=0, revision='old'),
    scenario('expected canary'),
    scenario('stalled above partition', updated=0, alert=True),
    scenario('unavailable canary', ready=1, alert=True),
    scenario('ordinary stalled rollout', partition=0, alert=True),
    scenario('missing metric fails open', partition=None, alert=True),
    scenario('all groups deliberately held', partition=2, updated=0),
    scenario('partition exceeds replica count', partition=3, updated=0),
    scenario('canary with extra updated groups', replicas=3, ready=3, updated=2),
    scenario('larger stalled canary', replicas=3, ready=3, updated=1, alert=True),
    scenario('still progressing', updated=1, partition=0, progressing=True),
    scenario('other cluster does not suppress', alert=True,
             partition_labels=labels | dict(cluster='other')),
    scenario('worker or unrelated set does not suppress', alert=True,
             partition_labels=labels | dict(statefulset='parent')),
    scenario('duplicate exporters with stalled rollout', duplicate=True, partition=0, alert=True),
    scenario('duplicate exporters with settled canary', duplicate=True),
    scenario('duplicate exporters with equal revisions', duplicate=True, revision='old', partition=0),
    scenario('different exporter cannot mask revision mismatch', duplicate=True, partition=0,
             replacement=dict(update_revision='old'), alert=True),
    scenario('one exporter still sees progress', duplicate=True, partition=0,
             replacement=dict(moving=True)),
    scenario('stale larger partition cannot mask a stall', duplicate=True, updated=0,
             replacement=dict(rollout_partition=2), alert=True),
    scenario('stale ready count cannot mask an unready canary', duplicate=True,
             replacement=dict(ready_count=1), alert=True),
    scenario('stale updated count cannot mask a stalled rollout', duplicate=True, partition=0,
             replacement=dict(updated_count=2, update_revision='old'), alert=True),
]
# KubeStatefulSetReplicasMismatch remains upstream and must still catch loss of
# readiness even after every replica has the new revision.
readiness_expr = '(kube_statefulset_status_replicas_ready != kube_statefulset_replicas) and (changes(kube_statefulset_status_replicas_updated[10m]) == 0)'
case = scenario('fully updated but unavailable', updated=2, ready=1, partition=0, revision='old')
case['metricsql_expr_test'].append({'expr': readiness_expr, 'eval_time': '20m', 'exp_samples': [
    {'labels': 'kube_statefulset_status_replicas_ready' + labelset(labels | dict(instance='old-exporter')),
     'value': 1}]})
cases.append(case)
with tempfile.TemporaryDirectory(prefix='statefulset-rules-') as directory:
    path = Path(directory)
    (path / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'test', 'rules': [
        {'alert': RULE, **spec, 'for': spec.get('for', '15m'), 'labels': {'severity': 'warning'}}]}]}))
    (path / 'tests.yaml').write_text(yaml.safe_dump({'rule_files': ['rules.yaml'],
                                                  'evaluation_interval': '1m', 'tests': cases}))
    subprocess.run([sys.argv[1] if len(sys.argv) > 1 else 'vmalert-tool',
                    'unittest', '-files', str(path / 'tests.yaml'), '-disableAlertgroupLabel'],
                   cwd=path, check=True)
print(f'{len(cases)} StatefulSet rollout scenarios passed')
