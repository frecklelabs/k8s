#!/usr/bin/env python3
"""Exercise the shared Job age alert with vmalert-tool and synthetic metrics.

Usage: python3 tests/observability/test_job_not_completed.py /path/to/vmalert-tool
"""
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / 'kubernetes/apps/observability/victoria-metrics/k8s-stack/helm/values.yaml'
NAME = 'KubeJobNotCompleted'
SPEC = yaml.safe_load(VALUES.read_text())['defaultRules']['rules'][NAME]['spec']
LABELS = dict(cluster='test', namespace='test', job_name='volsync-rsync-tls-dst-example')
COUNT = 41


def metric(name, values, labels):
    return dict(series=name + '{' + ','.join(f'{k}="{v}"' for k, v in labels.items()) + '}',
                values=' '.join(map(str, values)))


def case(name, *, ready=None, source=None, destination=None, ordinary=False,
         young=False, excluded=False, firing=()):
    labels = LABELS | (dict(job_name='ordinary-job') if ordinary else {})
    ksm = labels | dict(job='kube-state-metrics')
    inputs = [metric('kube_job_status_start_time', [0 if young else -50000] * COUNT, ksm),
              metric('kube_job_status_active', [1] * COUNT, ksm),
              metric('kube_job_status_ready', ready if ready is not None else [1] * COUNT, ksm)]
    if not ordinary:
        inputs.append(metric('kube_job_owner', [1] * COUNT, ksm | dict(
            owner_kind='ReplicationDestination', owner_is_controller='true', owner_name='example')))
    for role, values in [('source', source), ('destination', destination)]:
        if values != []:
            inputs.append(metric('volsync_volume_out_of_sync',
                                 values if values is not None else [0] * COUNT,
                                 dict(cluster='test', obj_namespace='test', obj_name='example',
                                      job='volsync-metrics', method='rsync-tls', role=role)))
    if excluded:
        inputs.append(metric('kube_job_labels', [1] * COUNT, ksm | dict(label_excluded_from_alerts='true')))
    expected = [dict(exp_labels=labels | dict(severity='warning'), exp_annotations={})]
    return dict(name=name, interval='20s', input_series=inputs,
                alert_rule_test=[dict(eval_time=f'{t * 20}s', alertname=NAME,
                                     exp_alerts=expected if t in firing else [])
                                 for t in range(COUNT)])


cases = [
    case('healthy old listener remains exempt'),
    case('one evaluation readiness transition', ready=[1] * 3 + [0] + [1] * 37),
    case('one evaluation controller telemetry gap',
         source=[0] * 3 + ['stale'] + [0] * 37,
         destination=[0] * 3 + ['stale'] + [0] * 37),
    case('readiness recovers before five minutes', ready=[0] * 15 + [1] * 26),
    case('sustained unready old listener', ready=[0] * COUNT, firing=range(15, COUNT)),
    case('missing source telemetry', source=[], firing=range(15, COUNT)),
    case('missing destination telemetry', destination=[], firing=range(15, COUNT)),
    case('out of sync source', source=[1] * COUNT, firing=range(15, COUNT)),
    case('out of sync destination', destination=[1] * COUNT, firing=range(15, COUNT)),
    case('ordinary old active job', ordinary=True, firing=range(15, COUNT)),
    case('young active job', ordinary=True, young=True),
    case('explicit exclusion remains exempt', ordinary=True, excluded=True),
    case('recovery resets hold before a later sustained failure',
         ready=[0] * 4 + [1] * 2 + [0] * 35, firing=range(21, COUNT)),
]
with tempfile.TemporaryDirectory(prefix='job-age-') as directory:
    path = Path(directory)
    (path / 'rules.yaml').write_text(yaml.safe_dump(dict(groups=[dict(name='test', rules=[
        dict(alert=NAME, **SPEC, labels=dict(severity='warning'))])])) )
    (path / 'tests.yaml').write_text(yaml.safe_dump(dict(rule_files=['rules.yaml'],
        evaluation_interval='20s', tests=cases)))
    subprocess.run([sys.argv[1] if len(sys.argv) > 1 else 'vmalert-tool',
                    'unittest', '-files', str(path / 'tests.yaml'), '-disableAlertgroupLabel'],
                   cwd=path, check=True)
print(f'Passed {len(cases)} Job age alert cases')
