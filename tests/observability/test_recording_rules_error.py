#!/usr/bin/env python3
"""Test the shared RecordingRulesError override with vmalert-tool (PyYAML).

Usage: python3 tests/observability/test_recording_rules_error.py /path/to/vmalert-tool
Synthetic counters use a 30s scrape and evaluation cadence. Historical incident
queries and labels stay in the private review packet.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / 'kubernetes/apps/observability/victoria-metrics/k8s-stack/helm/values.yaml'
NAME = 'RecordingRulesError'
SPEC = yaml.safe_load(VALUES.read_text())['defaultRules']['rules'][NAME]['spec']
LABELS = dict(cluster='test', recording='test_recording', group='test_group')


def series(values, **extra):
    labels = LABELS | extra
    return dict(series='vmalert_recording_rules_errors_total{' + ','.join(
        f'{k}="{v}"' for k, v in labels.items()) + '}',
        values=' '.join(map(str, values)))


def case(name, values, firing=(), extra=()):
    expected = [dict(exp_labels=LABELS | dict(severity='warning'), exp_annotations={})]
    return dict(name=name, interval='30s',
                input_series=[series(values, id='a'), *extra],
                alert_rule_test=[dict(eval_time=f'{t * 30}s', alertname=NAME,
                                     exp_alerts=expected if t in firing else [])
                                 for t in range(41)])


cases = [
    case('healthy', [0] * 41),
    case('two startup failures then recovery', [0, 1] + [2] * 39),
    case('two minute rollout burst then recovery', [0, 1, 2, 3, 4] + [4] * 36),
    case('sustained failures page after five minute hold', range(41), range(11, 41)),
    case('repeated failures every ninety seconds', [t // 3 for t in range(41)], range(13, 41)),
    case('isolated errors every four minutes reset hold', [t // 8 for t in range(41)]),
    case('counter reset without new errors', [9] * 5 + [0] * 36),
    case('counter reset during sustained failures',
         list(range(15)) + list(range(26)), range(11, 41)),
    case('recovery clears firing and next burst starts fresh',
         list(range(15)) + [14] * 10 + [15] * 16, range(11, 18)),
    # A rule id change keeps the existing aggregation identity and hold.
    case('replacement rule id during ongoing failures',
         list(range(15)) + ['stale'] + ['_'] * 25, range(11, 41),
         extra=[series(['_'] * 14 + list(range(27)), id='b')]),
]
rule = dict(alert=NAME, **SPEC, labels=dict(severity='warning'))
with tempfile.TemporaryDirectory(prefix='recording-errors-') as directory:
    path = Path(directory)
    (path / 'rules.yaml').write_text(yaml.safe_dump(dict(groups=[dict(name='test', rules=[rule])])))
    (path / 'tests.yaml').write_text(yaml.safe_dump(dict(rule_files=['rules.yaml'],
        evaluation_interval='30s', tests=cases)))
    subprocess.run([sys.argv[1] if len(sys.argv) > 1 else 'vmalert-tool',
                    'unittest', '-files', str(path / 'tests.yaml'), '-disableAlertgroupLabel'],
                   cwd=path, check=True)
print(f'Passed {len(cases)} recording error alert cases')
