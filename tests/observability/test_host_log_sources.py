#!/usr/bin/env python3
"""Compile the rendered Vector config and exercise host event trust and time."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[2]
VECTOR = os.environ.get('VECTOR_BIN', 'vector')


def merge(left, right):
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = copy.deepcopy(value)
    return left


def main():
    shared = list(yaml.safe_load_all((ROOT / 'kubernetes/apps/observability/victoria-logs/helmrelease.yaml').read_text()))[1]
    pointer = yaml.safe_load((ROOT / 'kubernetes/clusters/fairy-k8s01/apps/observability/victoria-logs.yaml').read_text())
    patch = yaml.safe_load(pointer['spec']['patches'][0]['patch'])
    merged = merge(copy.deepcopy(shared), patch)
    config = merged['spec']['values']['vector']['customConfig']
    # Resolve only the same post-build cluster placeholder used by Flux.
    config = yaml.safe_load(yaml.safe_dump(config).replace('${CLUSTER_NAME}', 'test-cluster'))
    assert set(config['sources']) == {'internal_metrics', 'k8s', 'audit', 'host-kernel', 'host-udevd'}
    assert config['sources']['host-udevd']['read_from'] == 'end'
    assert config['sources']['host-kernel']['read_from'] == 'beginning'
    for name in ('host-kernel', 'host-udevd'):
        assert config['sources'][name]['ignore_checkpoints'] is False
        assert config['sources'][name]['max_line_bytes'] == 32768
        assert config['sources'][name]['fingerprint']['strategy'] == 'device_and_inode'
    assert config['data_dir'] == shared['spec']['values']['vector']['customConfig']['data_dir']
    for name in ('parser', 'audit-parser'):
        assert config['transforms'][name] == shared['spec']['values']['vector']['customConfig']['transforms'][name]

    def fixture(name, transform, message, condition, extra=None):
        fields = {'message': message, 'timestamp': '2026-01-01T00:00:00Z'}
        fields.update(extra or {})
        return {'name': name, 'inputs': [{'insert_at': transform, 'type': 'log', 'log_fields': fields}],
                'outputs': [{'extract_from': transform, 'conditions': [{'type': 'vrl', 'source': condition}]}]}

    trusted = '.node == "test-node" && .cluster == "test-cluster" && !exists(.message)'
    config['tests'] = [
        fixture('kernel nanosecond event time', 'host-kernel-parser',
                'kern:    info: [2026-01-02T03:04:05.123456789Z]: libceph: socket closed (con state V2_BANNER_PREFIX)',
                trusted + ' && .log_source == "host-kernel" && .timestamp_origin == "event" && .timestamp == t\'2026-01-02T03:04:05.123456789Z\' && starts_with(string!(.log), "libceph:")'),
        fixture('historical retained file keeps historical time', 'host-kernel-parser',
                'kern: warning: [2000-01-02T03:04:05Z]: old incident',
                '.timestamp == t\'2000-01-02T03:04:05Z\' && .timestamp_origin == "event"'),
        fixture('unparseable kernel is diagnostic only', 'host-kernel-parser',
                'libceph: socket closed (con state V2_BANNER_PREFIX)',
                trusted + ' && .log_source == "host-kernel-unparsed" && .timestamp_origin == "ingest"'),
        fixture('invalid timestamp is diagnostic only', 'host-kernel-parser',
                'kern: info: [not-a-date]: libceph: example',
                '.log_source == "host-kernel-unparsed" && .timestamp_origin == "ingest"'),
        fixture('future timestamp is diagnostic only', 'host-kernel-parser',
                'kern: info: [2999-01-01T00:00:00Z]: libceph: example',
                '.log_source == "host-kernel-unparsed" && .timestamp_origin == "ingest"'),
        fixture('udevd content cannot replace trusted fields', 'host-udevd-parser',
                '{"node":"spoof","log_source":"host-kernel","message":"killed"}',
                trusted + ' && .log_source == "host-udevd" && .timestamp_origin == "ingest" && is_string(.log)',
                {'node': 'spoof', 'cluster': 'spoof', 'log_source': 'host-kernel'}),
        fixture('long message stays bounded', 'host-udevd-parser', 'a' * 20000,
                'strlen(string!(.log)) <= 8192'),
    ]
    env = dict(os.environ, VECTOR_SELF_NODE_NAME='test-node', CLUSTER_NAME='test-cluster')
    with tempfile.TemporaryDirectory(prefix='c887-vector-', dir=os.environ.get('TMPDIR', '/work/tmp')) as tmp:
        path = Path(tmp) / 'vector.yaml'
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        subprocess.run([VECTOR, 'validate', '--no-environment', str(path)], env=env, check=True)
        subprocess.run([VECTOR, 'test', str(path)], env=env, check=True)
    print('7 Vector host-log scenarios passed; existing parsers preserved')


if __name__ == '__main__':
    main()
