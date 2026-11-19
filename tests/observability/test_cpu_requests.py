#!/usr/bin/env python3
"""Test the generated CPU recording: python test_cpu_requests.py rules.yaml.

Requires PyYAML and promtool. Render the chart version from the OCIRepository
with the repository values, then run that chart's sync-job in OUTPUT mode on
the generated config. Pass its VMRule output here; testing the generated rule
catches overrides silently ignored by the chart. Scheduler inputs are effective
request fixtures, not a second implementation of Kubernetes PodRequests.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

RECORD = 'namespace_cpu:kube_pod_container_resource_requests:sum'
rules = [rule for doc in yaml.safe_load_all(Path(sys.argv[1]).read_text())
         if doc and doc.get('kind') == 'VMRule'
         for group in doc['spec']['groups'] for rule in group['rules']
         if rule.get('record') == RECORD]
assert len(rules) == 1, f'expected one rendered CPU recording, found {len(rules)}'
rule = rules[0]


def labels(values):
    return '{' + ','.join(f'{k}={json.dumps(v)}' for k, v in sorted(values.items())) + '}'


def case(name, effective, expected, *, phase='Running', replicas=(1, 1),
         phase_value=1, phase_job='kube-state-metrics', extra=()):
    identity = dict(cluster='cluster-a', namespace='work', pod='subject')
    series = []
    for i, multiplier in enumerate(replicas):
        series.append({'series': 'kube_pod_resource_request' + labels(dict(
            identity, resource='cpu', unit='cores', job='kube-scheduler',
            instance=f'scheduler-{i}', node=f'node-{i}')),
            'values': f'{effective * multiplier}+0x5'})
    # KSM replicas must not multiply a pod's reservation either.
    for i in range(2):
        series.append({'series': 'kube_pod_status_phase' + labels(dict(
            identity, phase=phase, job=phase_job, instance=f'ksm-{i}')),
            'values': f'{phase_value}+0x5'})
    # Decoys prove the recording selects the scheduler's CPU units and job.
    for change in [dict(resource='memory'), dict(unit='core'), dict(job='other')]:
        series.append({'series': 'kube_pod_resource_request' + labels(dict(
            identity, **(dict(resource='cpu', unit='cores', job='kube-scheduler') | change))),
            'values': '99+0x5'})
    series.extend(extra)
    samples = [] if expected is None else [dict(labels=labels(dict(
        __name__=RECORD, cluster='cluster-a', namespace='work')), value=expected)]
    return dict(name=name, interval='20s', input_series=series,
                promql_expr_test=[dict(expr=RECORD, eval_time='1m', exp_samples=samples)])


cases = [
    case('regular containers 200m + 300m', .5, .5),
    case('native sidecar 500m overlaps regular 500m', 1, 1),
    case('ordinary init max(200m + 300m, 800m, 600m)', .8, .8),
    case('ordinary init below regular sum max(700m, 400m)', .7, .7),
    # Init order is significant: ordinary init only overlaps preceding sidecars.
    case('mixed init: sidecar 200m then init 800m then sidecar 300m; app 100m', 1, 1),
    case('mixed reordered: init 800m then sidecars 200m + 300m; app 100m', .8, .8),
    case('pod overhead adds 50m to effective 800m', .85, .85),
    case('max replica dedup retains larger reservation', .5, 1, replicas=(1, 2)),
    case('Pending included', .5, .5, phase='Pending'),
    case('zero requests retained', 0, 0),
    case('Succeeded excluded', .5, None, phase='Succeeded'),
    case('Failed excluded', .5, None, phase='Failed'),
    case('Unknown excluded', .5, None, phase='Unknown'),
    case('inactive Running phase excluded', .5, None, phase_value=0),
    case('other phase exporter excluded', .5, None, phase_job='other'),
]

# Matching names in other clusters/namespaces must not supply the phase.
for key, value in [('cluster', 'cluster-b'), ('namespace', 'other'), ('pod', 'other')]:
    identity = dict(cluster='cluster-a', namespace='work', pod='subject') | {key: value}
    extra = [dict(series='kube_pod_status_phase' + labels(dict(
        identity, phase='Running', job='kube-state-metrics')), values='1+0x5')]
    cases.append(case(f'phase isolated by {key}', .5, None, phase='Unknown', extra=extra))

# Same namespace and pod names on two clusters, plus another namespace and pod:
# grouping must sum pods, retain namespaces and never combine clusters.
identity_case = case('sum pods with cluster and namespace isolation', .5, 1)
for cluster, namespace, pod, value in [('cluster-a', 'work', 'second', .5),
                                      ('cluster-a', 'other', 'subject', 2),
                                      ('cluster-b', 'work', 'subject', 3)]:
    identity = dict(cluster=cluster, namespace=namespace, pod=pod)
    for metric, rest, amount in [
        ('kube_pod_resource_request', dict(resource='cpu', unit='cores', job='kube-scheduler'), value),
        ('kube_pod_status_phase', dict(phase='Running', job='kube-state-metrics'), 1),
    ]:
        identity_case['input_series'].append(dict(series=metric + labels(identity | rest),
                                                 values=f'{amount}+0x5'))
identity_case['promql_expr_test'][0]['exp_samples'].extend([
    dict(labels=labels(dict(__name__=RECORD, cluster='cluster-a', namespace='other')), value=2),
    dict(labels=labels(dict(__name__=RECORD, cluster='cluster-b', namespace='work')), value=3),
])
cases.append(identity_case)

# Missing scheduler telemetry is absent, never an invented zero reservation.
missing = case('missing scheduler input is absent', .5, None, replicas=())
cases.append(missing)

with tempfile.TemporaryDirectory(prefix='cpu-requests-') as directory:
    path = Path(directory)
    (path / 'rules.yaml').write_text(yaml.safe_dump(dict(groups=[dict(name='cpu', rules=[rule])])))
    (path / 'tests.yaml').write_text(yaml.safe_dump(dict(
        rule_files=['rules.yaml'], evaluation_interval='20s', tests=cases)))
    subprocess.run(['promtool', 'test', 'rules', 'tests.yaml'], cwd=path, check=True)
print(f'{len(cases)} rendered CPU recording scenarios passed')
