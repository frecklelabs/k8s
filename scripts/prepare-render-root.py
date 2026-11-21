#!/usr/bin/env python3
"""Prepare one cluster's checkout for the offline render check (render.yaml).

Writes two ephemeral files that are never committed:

1. The root kustomization prepare-image-pull-root.py writes, so flate's
   discovery stays inside this cluster.
2. kubernetes/clusters/<cluster>/flux/zz-render-placeholders.yaml, holding
   the objects that exist only in the live cluster but that Flux
   Kustomizations read during postBuild substitution or wait on:
   - k8s-cluster-info in every namespace, with the cluster's real values.
     Reflector mirrors default/k8s-cluster-info into every namespace live;
     flate has no Reflector, so a namespace without an in-repo copy
     blocks. An in-repo copy wins over this one.
   - k8s-cluster-secrets in every namespace, with a placeholder per key the
     cluster's ExternalSecret templates (Reflector mirrors that one too).
   - ci/render-placeholders/<cluster>.yaml, the hand-kept rest: ConfigMaps
     the private repo creates, and stubs for private Kustomizations that a
     public one depends on.

Placeholder values only have to render. They are never real addresses.
"""

import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module  # noqa: E402

prepare_root = import_module('prepare-image-pull-root').prepare

OUTPUT = 'zz-render-placeholders.yaml'
NAME = re.compile(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$')


def documents(path):
    return re.split(r'(?m)^---[^\n]*\n', path.read_text())


def namespaces(cluster_dir):
    found = {'default', 'flux-system'}
    for path in sorted((cluster_dir / 'flux').glob('*.y*ml')):
        for doc in documents(path):
            if re.search(r'(?m)^kind:\s*Namespace\s*$', doc):
                m = re.search(r'(?m)^metadata:\s*\n(?:[ \t]+.*\n)*?[ \t]+name:\s*["\']?([^\s"\']+)', doc)
                if m:
                    found.add(m.group(1))
            m = re.search(r'(?m)^metadata:\s*\n(?:[ \t]+.*\n)*?[ \t]+namespace:\s*(?:&\S+\s+)?["\']?([^\s"\']+)', doc)
            if m:
                found.add(m.group(1))
    apps = cluster_dir / 'apps'
    if apps.is_dir():
        found.update(p.name for p in apps.iterdir() if p.is_dir() and not p.name.startswith('_'))
    bad = sorted(n for n in found if not NAME.match(n))
    if bad:
        raise ValueError(f'unexpected namespace names: {bad}')
    return sorted(found)


def cluster_info(cluster_dir):
    path = cluster_dir / 'apps' / '_shared' / 'cluster-info' / 'k8s-cluster-info.yaml'
    data = {}
    in_data = False
    for line in path.read_text().splitlines():
        if re.match(r'^data:\s*$', line):
            in_data = True
            continue
        if in_data and re.match(r'^\S', line):
            break
        m = re.match(r'^\s+([A-Z][A-Z0-9_]*):\s*["\']?([^"\'#]*?)["\']?\s*(#.*)?$', line)
        if in_data and m:
            data[m.group(1)] = m.group(2)
    if 'CLUSTER_NAME' not in data:
        raise ValueError(f'no CLUSTER_NAME in {path}')
    return data


def secret_keys(cluster_dir):
    path = cluster_dir / 'apps' / 'default' / 'k8s-cluster-secrets.yaml'
    if not path.exists():
        return []
    return sorted(set(re.findall(r'(?m)^\s+([A-Z][A-Z0-9_]*):\s*["\']?\{\{', path.read_text())))


def placeholders(root, cluster):
    cluster_dir = root / 'kubernetes' / 'clusters' / cluster
    info = cluster_info(cluster_dir)
    keys = secret_keys(cluster_dir)
    docs = []
    for ns in namespaces(cluster_dir):
        docs.append({'apiVersion': 'v1', 'kind': 'ConfigMap',
                     'metadata': {'name': 'k8s-cluster-info', 'namespace': ns},
                     'data': info})
        docs.append({'apiVersion': 'v1', 'kind': 'Secret',
                     'metadata': {'name': 'k8s-cluster-secrets', 'namespace': ns},
                     'stringData': {k: f'{k.lower().replace("_", "-")}.render-placeholder.invalid'
                                    for k in keys}})
    # JSON is YAML; nothing here parses or rewrites a production manifest.
    out = '\n---\n'.join(json.dumps(d, indent=2, sort_keys=True) for d in docs) + '\n'
    extra = root / 'ci' / 'render-placeholders' / f'{cluster}.yaml'
    if extra.exists():
        out += '---\n' + extra.read_text()
    return out


def prepare(root, cluster):
    root = Path(root).resolve()
    target = root / 'kubernetes' / 'clusters' / cluster / 'flux' / OUTPUT
    if target.exists():
        raise ValueError(f'refusing to replace existing {target}')
    content = placeholders(root, cluster)
    prepare_root(root, cluster)
    target.write_text(content)
    return target


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('usage: prepare-render-root.py <cluster>')
    print(prepare(Path.cwd(), sys.argv[1]))
