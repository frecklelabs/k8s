#!/usr/bin/env python3
"""Evaluate the Flux-rendered rule with local VictoriaLogs and vmalert binaries."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'kubernetes/apps/observability/node-storage-log-alerts'
POINTER = ROOT / 'kubernetes/clusters/fairy-k8s01/apps/observability/node-storage-log-alerts.yaml'
VLOGS = os.environ.get('VLOGS_BIN', 'victoria-logs-prod')
VMALERT = os.environ.get('VMALERT_BIN', 'vmalert-prod')
CLUSTER = 'test-cluster'
ALERT = 'NodeCephHandshakeFailures'
CLOSE = 'libceph: osd0 TEST-PEER socket closed (con state V2_BANNER_PREFIX)'


def render():
    output = subprocess.check_output([
        'flux', '-n', 'observability', 'build', 'kustomization', 'node-storage-log-alerts',
        '--dry-run', '--path', str(APP), '--kustomization-file', str(POINTER),
    ], text=True)
    output = subprocess.check_output(['flux', 'envsubst', '--strict'], input=output, text=True,
                                     env={**os.environ, 'CLUSTER_NAME': CLUSTER})
    resources = {doc['kind']: doc for doc in yaml.safe_load_all(output) if doc}
    assert set(resources) == {'ConfigMap', 'Deployment', 'VMPodScrape'}
    config = resources['ConfigMap']
    template = resources['Deployment']['spec']['template']
    pod = template['spec']
    scrape = resources['VMPodScrape']['spec']
    assert pod['volumes'][0]['configMap']['name'] == config['metadata']['name']
    assert scrape['selector']['matchLabels'] == template['metadata']['labels']
    assert scrape['podMetricsEndpoints'][0]['port'] == pod['containers'][0]['ports'][0]['name']
    return config['data']['rules.yaml']


def fixture(now):
    def events(node, ages=(90, 91, 92), **overrides):
        return [{'_time': (now - timedelta(seconds=age)).isoformat(), '_msg': CLOSE,
                 'log_source': 'host-kernel', 'timestamp_origin': 'event',
                 'cluster': CLUSTER, 'node': node, **overrides} for age in ages]

    return (events('repeated') + events('repeated', (90,))
            + events('suffix', _msg=CLOSE + ' (-ECONNRESET)')
            + events('two-only', (90, 91)) + events('duplicates', (90, 90, 90))
            + events('old', (361, 362, 363)) + events('just-expired', (301, 302, 303))
            + events('')
            + events('wrong-cluster', cluster='other') + events('missing-cluster', cluster='')
            + events('diagnostic', log_source='host-kernel-unparsed')
            + events('ingest-time', timestamp_origin='ingest')
            + events('udevd', log_source='host-udevd')
            + events('transcript', log_source='', _msg='transcript: ' + CLOSE)
            + events('quoted-message', _msg='transcript: ' + CLOSE)
            + events('open', _msg=CLOSE.replace('V2_BANNER_PREFIX', 'OPEN')))


def local_address():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return f'127.0.0.1:{listener.getsockname()[1]}'


def wait_for(url, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if predicate(response):
                    return
        except URLError:
            pass
        time.sleep(0.2)
    raise AssertionError(f'Timed out waiting for {url}')


def run():
    config = render()
    expr = yaml.safe_load(config)['groups'][0]['rules'][0]['expr']
    logs_addr, alert_addr = local_address(), local_address()
    with tempfile.TemporaryDirectory() as path:
        directory = Path(path)
        rule_file = directory / 'rules.yaml'
        rule_file.write_text(config)
        with (directory / 'vlogs.log').open('w+') as logs_output, (directory / 'alert.log').open('w+') as alert_output:
            logs = subprocess.Popen([VLOGS, f'-httpListenAddr={logs_addr}',
                                     f'-storageDataPath={directory / "storage"}', '-inmemoryDataFlushInterval=1s'],
                                    stdout=logs_output, stderr=subprocess.STDOUT)
            evaluator = None
            try:
                wait_for(f'http://{logs_addr}/health', lambda response: response.status == 200, 10)
                now = datetime.now(timezone.utc)
                data = '\n'.join(map(json.dumps, fixture(now))).encode() + b'\n'
                ingest = '/insert/jsonline?_stream_fields=log_source,cluster,node'
                request = Request(f'http://{logs_addr}{ingest}', data=data,
                                  headers={'Content-Type': 'application/stream+json'})
                with urlopen(request, timeout=5) as response:
                    assert response.status == 200
                params = urlencode({'query': expr, 'time': now.isoformat()})
                expected = {'repeated': '3', 'suffix': '3'}

                def exact_series(response):
                    rows = json.load(response)['data']['result']
                    return (len(rows) == len(expected)
                            and all(row['metric']['cluster'] == CLUSTER for row in rows)
                            and {row['metric']['node']: row['value'][1] for row in rows} == expected)

                wait_for(f'http://{logs_addr}/select/logsql/stats_query?{params}', exact_series, 10)
                evaluator = subprocess.Popen([VMALERT, f'-rule={rule_file}', f'-httpListenAddr={alert_addr}',
                                              f'-datasource.url=http://{logs_addr}', '-notifier.blackhole',
                                              '-group.maxStartDelay=1ms'],
                                             stdout=alert_output, stderr=subprocess.STDOUT)

                def exact_alerts(response):
                    alerts = json.load(response)['data']['alerts']
                    if not alerts:
                        return False
                    nodes = sorted(alert['labels']['node'] for alert in alerts)
                    assert nodes == sorted(expected), f'Unexpected first firing set: {nodes}'
                    assert all(alert['labels']['alertname'] == ALERT and alert['state'] == 'firing'
                               and alert['labels']['cluster'] == CLUSTER
                               and alert['labels']['severity'] == 'warning' for alert in alerts)
                    return True

                wait_for(f'http://{alert_addr}/api/v1/alerts', exact_alerts, 10)
            except Exception:
                for output in (logs_output, alert_output):
                    output.seek(0)
                    print(output.read()[-4000:])
                raise
            finally:
                for process in (evaluator, logs):
                    if process is not None:
                        process.terminate()
                        process.wait(timeout=10)


if __name__ == '__main__':
    run()
    print('PASS: rendered rule groups distinct recent kernel closes by node; negative fixtures stay quiet')
