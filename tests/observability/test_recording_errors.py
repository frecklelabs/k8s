#!/usr/bin/env python3
"""Replay a generated RecordingRulesError with vmalert-tool (requires PyYAML).

Usage: python3 tests/observability/test_recording_errors.py rules.yaml vmalert-tool
Pass the chart sync-job's OUTPUT VMRule YAML, so ignored overrides fail the test.
All fixtures use synthetic labels; the startup burst follows a two-error incident.
"""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

matches = [(g, r) for doc in yaml.safe_load_all(Path(sys.argv[1]).read_text())
           if doc and doc.get('kind') == 'VMRule'
           for g in doc['spec']['groups'] for r in g['rules']
           if r.get('alert') == 'RecordingRulesError']
assert len(matches) == 1, f'expected one generated rule, found {len(matches)}'
group, rule = deepcopy(matches[0])
assert rule['for'] == '10m', f'override ignored: {rule["for"]}'
rule['annotations'] = {}
baseline = deepcopy(rule)
baseline.update(alert='BaselineRecordingRulesError', **{'for': '5m'})
BASE = dict(cluster='test', pod='vmalert-test', instance='test:8080',
            recording='test_recording', group='recording-group', file='rules.yaml')


def counter(values, **labels):
    labels = BASE | labels
    return dict(series='vmalert_recording_rules_errors_total{' + ','.join(
        f'{k}="{v}"' for k, v in labels.items()) + '}',
        values=' '.join(str(v) for v in values))


def case(name, values, *, fire_from=None, fire_until=3030, extra=(), baseline_startup=False):
    labels = BASE | rule.get('labels', {})
    expected = [dict(exp_labels=labels, exp_annotations={})]
    checks = [dict(eval_time=f'{t}s', alertname=rule['alert'],
                   exp_alerts=expected if fire_from is not None and
                   fire_from <= t < fire_until else []) for t in range(0, 3001, 30)]
    if baseline_startup:
        checks += [dict(eval_time='330s', alertname=baseline['alert'], exp_alerts=expected)]
    return dict(name=name, interval='30s', input_series=[counter(values, id='one'), *extra],
                alert_rule_test=checks)


cases = [
    case('healthy flat counter', [0] * 101),
    # First positive scrape at t=30s, second at t=60s; healthy thereafter.
    case('recovered two-error startup burst', [0, 1] + [2] * 99, baseline_startup=True),
    case('already nonzero counter stays flat', [2] * 101),
    case('continuous failures', list(range(101)), fire_from=630),
    # A two-minute recent-error gate would miss these ongoing failures.
    case('intermittent error every four minutes', [i // 8 for i in range(101)], fire_from=840),
    case('isolated errors fifteen minutes apart', [i // 30 for i in range(101)]),
    case('counter reset without new errors', [2] * 20 + [0] * 81),
    case('reset then continuous failure', [2] * 20 + list(range(81)), fire_from=1230),
    case('ongoing failure recovers', list(range(21)) + [20] * 80,
         fire_from=630, fire_until=900),
    # Different rule IDs are deliberately collapsed by the upstream expression.
    case('rule id change preserves intermittent detection',
         [i // 8 for i in range(41)] + [5] * 60, fire_from=840,
         extra=[counter([0] * 40 + [(i - 40) // 8 for i in range(40, 101)], id='two')]),
    case('other recording cannot extend a startup burst', [0, 1] + [2] * 99,
         extra=[counter([0] * 20 + [1] * 81, id='two', recording='other_recording')]),
]

with tempfile.TemporaryDirectory(prefix='recording-errors-') as directory:
    path = Path(directory)
    (path / 'rules.yaml').write_text(yaml.safe_dump(dict(groups=[dict(
        name=group['name'], interval=group.get('interval', '30s'), rules=[rule, baseline])])))
    (path / 'tests.yaml').write_text(yaml.safe_dump(dict(
        rule_files=['rules.yaml'], evaluation_interval='30s', tests=cases)))
    subprocess.run([sys.argv[2] if len(sys.argv) > 2 else 'vmalert-tool', 'unittest',
                    '-files', str(path / 'tests.yaml'), '-disableAlertgroupLabel'], cwd=path, check=True)
print(f'Passed {len(cases)} generated RecordingRulesError scenarios')
