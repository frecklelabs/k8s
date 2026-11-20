#!/usr/bin/env python3
"""Exercise the actual VMRule with vmalert-tool and synthetic 20s KSM samples.

Usage: python3 tests/observability/test_container_creating.py /path/to/vmalert-tool
Requires PyYAML. Sparse samples deliberately omit stale markers to reproduce
instant-selector lookback; both regular and init containers must expire.
"""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'kubernetes/apps/observability/victoria-metrics/k8s-stack/node-storage-rules.yaml'
GROUP = yaml.safe_load(SOURCE.read_text())['spec']['groups'][0]
RULE = next(r for r in GROUP['rules'] if r.get('alert') == 'KubePodContainerCreating')
BASE = dict(cluster='test', namespace='test', pod='test-pod', job='kube-state-metrics')
METRICS = ['kube_pod_container_status_waiting_reason',
           'kube_pod_init_container_status_waiting_reason']


def series(metric, values, **labels):
    return dict(series=metric + '{' + ','.join(
        f'{k}="{v}"' for k, v in (BASE | labels).items()) + '}', values=values)


def case(name, inputs, *, fires_at=None, clears_at=999, join=True):
    if join:
        inputs = inputs + [series('kube_pod_info', '1+0x20', node='test-node')]
    labels = {k: BASE[k] for k in ('cluster', 'namespace', 'pod')}
    labels |= dict(node='test-node', severity='warning')
    expected = [dict(exp_labels=labels, exp_annotations={})]
    return dict(name=name, interval='20s', input_series=inputs,
                alert_rule_test=[dict(eval_time=f'{t}s', alertname=RULE['alert'],
                    exp_alerts=expected if fires_at is not None and fires_at <= t < clears_at else [])
                    for t in range(0, 301, 30)])


cases = []
for metric in METRICS:
    def wait(values, **labels):
        return series(metric, values, container='test-container', reason='ContainerCreating', **labels)
    cases += [
        case(f'{metric}: lone sample without stale marker', [wait('1')]),
        case(f'{metric}: explicit staleness', [wait('1 stale')]),
        case(f'{metric}: sustained wait', [wait('1+0x20')], fires_at=120),
        case(f'{metric}: missing one scrape', [wait('1 1 _ 1+0x17')], fires_at=120),
        case(f'{metric}: wait ends', [wait('1+0x7 0+0x12')], fires_at=120, clears_at=180),
        case(f'{metric}: scrape stops', [wait('1+0x6')], fires_at=120, clears_at=180),
        case(f'{metric}: fresh zero cannot refresh stale one',
             [wait('1'), wait('0+0x20', instance='replacement')]),
        case(f'{metric}: other reason', [series(metric, '1+0x20',
             container='test-container', reason='ErrImagePull')]),
        case(f'{metric}: no node identity', [wait('1+0x20')], join=False),
        case(f'{metric}: replacement pods cannot pool hold',
             [wait('1+0x2 stale'), wait('_ _ _ 1+0x2 stale', pod='replacement')]),
    ]
# A fresh init zero must not keep a stale regular one alive, or vice versa.
for stale, fresh in [METRICS, METRICS[::-1]]:
    cases.append(case('cross-metric freshness: ' + stale, [
        series(stale, '1', container='old', reason='ContainerCreating'),
        series(fresh, '0+0x20', container='new', reason='ContainerCreating')]))

rule = deepcopy(RULE)
rule['annotations'] = {}
with tempfile.TemporaryDirectory(prefix='container-creating-') as directory:
    tmp = Path(directory)
    (tmp / 'rules.yaml').write_text(yaml.safe_dump(dict(groups=[dict(
        name=GROUP['name'], interval=GROUP['interval'], rules=[rule])]), sort_keys=False))
    (tmp / 'tests.yaml').write_text(yaml.safe_dump(dict(rule_files=['rules.yaml'],
        evaluation_interval=GROUP['interval'], tests=cases), sort_keys=False))
    subprocess.run([sys.argv[1] if len(sys.argv) > 1 else 'vmalert-tool', 'unittest',
                    '-files', str(tmp / 'tests.yaml'), '-disableAlertgroupLabel'],
                   cwd=tmp, check=True)
print(f'Passed {len(cases)} ContainerCreating alert cases')
