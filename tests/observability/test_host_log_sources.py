#!/usr/bin/env python3
"""Exercise the Flux- and Helm-rendered Vector config; pass the pinned chart archive."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[2]
VECTOR = os.environ.get('VECTOR_BIN', 'vector')
HELM = os.environ.get('HELM_BIN', 'helm')
APP = ROOT / 'kubernetes/apps/observability/victoria-logs'
POINTER = ROOT / 'kubernetes/clusters/fairy-k8s01/apps/observability/victoria-logs.yaml'


def render(chart, directory):
    flux_output = subprocess.check_output([
        'flux', '-n', 'observability', 'build', 'kustomization', 'victoria-logs',
        '--dry-run', '--path', str(APP), '--kustomization-file', str(POINTER),
    ], text=True)
    resources = {doc['kind']: doc for doc in yaml.safe_load_all(flux_output) if doc}
    values = resources['HelmRelease']['spec']['values']
    values_path = directory / 'values.yaml'
    values_path.write_text(yaml.safe_dump(values).replace('${CLUSTER_NAME}', 'test-cluster'))
    metadata = yaml.safe_load(subprocess.check_output([HELM, 'show', 'chart', chart], text=True))
    assert metadata['version'] == str(resources['OCIRepository']['spec']['ref']['tag'])
    output = subprocess.check_output([
        HELM, 'template', 'victoria-logs', chart, '--namespace', 'observability',
        '--kube-version', '1.37.2', '--values', str(values_path),
    ], text=True)
    configmaps = [doc for doc in yaml.safe_load_all(output) if doc and doc['kind'] == 'ConfigMap'
                  and doc['metadata']['name'] == 'vector-vl-config']
    assert len(configmaps) == 1
    return yaml.safe_load(configmaps[0]['data']['victoria-logs-vector.yaml'])


def check_topology(config):
    assert set(config['sources']) == {'internal_metrics', 'k8s', 'audit', 'host-kernel', 'host-udevd'}
    assert config['sources']['host-udevd']['read_from'] == 'end'
    assert config['sources']['host-kernel']['read_from'] == 'beginning'
    for name in ('host-kernel', 'host-udevd'):
        assert config['sources'][name]['ignore_checkpoints'] is False
        assert config['sources'][name]['max_line_bytes'] == 32768
        assert config['sources'][name]['fingerprint']['strategy'] == 'device_and_inode'
    assert config['data_dir'] == '/vector-data-dir'
    sinks = config['sinks']
    host = sinks['host-logs-0']
    assert host['inputs'] == ['host-kernel-parser', 'host-udevd-parser']
    assert host['endpoints'] == sinks['vlogs-0']['endpoints']
    headers = host['request']['headers']
    assert headers['VL-Stream-Fields'] == 'log_source,cluster,node'
    assert headers['VL-Time-Field'] == 'timestamp'
    assert headers['VL-Msg-Field'] == 'log'
    for transform, sink in [('parser', 'vlogs-0'), ('host-kernel-parser', 'host-logs-0'),
                            ('host-udevd-parser', 'host-logs-0')]:
        assert [name for name, spec in sinks.items() if transform in spec['inputs']] == [sink]


def check_parsers(config, directory):

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
    path = directory / 'vector.yaml'
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    subprocess.run([VECTOR, 'validate', '--no-environment', str(path)], env=env, check=True)
    subprocess.run([VECTOR, 'test', str(path)], env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('chart', help='local victoria-logs-single chart archive pinned by Flux')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='host-log-test-') as tmp:
        directory = Path(tmp)
        config = render(args.chart, directory)
        check_topology(config)
        check_parsers(config, directory)
    print('Rendered Vector topology and 7 host-log scenarios passed')


if __name__ == '__main__':
    main()
