// Post-upgrade task for npm updates (config.json5, cockpit C-3229).
//
// Renovate updates npm lockfiles through the cluster's package proxy, and
// npm records the proxy's tarball URLs, which only resolve in-cluster, as
// "resolved" (e.g. http://package-proxy...:8080/npm/@scope%2Fname/-/name-1.0.0.tgz).
// CI's `npm ci` then can't fetch them. Rewrite them to the public registry
// URL npm would have written (https://registry.npmjs.org/@scope/name/-/...);
// integrity hashes are unchanged, and npm's default replace-registry-host
// still routes in-cluster installs of the rewritten lockfile through the proxy.
//
// Runs in the repo checkout (cwd) and rewrites every package-lock.json and
// npm-shrinkwrap.json in it, outside node_modules. Idempotent.
//
// The body runs unconditionally: an `import.meta.url === argv[1]` main guard
// is always false here, because /config is a whole-volume ConfigMap mount
// whose files are symlinks into ..data/, and Node resolves the main module's
// URL to the symlink target.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const PROXY = /http:\/\/package-proxy\.dev-platform\.svc\.cluster\.local:8080\/npm\/(@[^/"%]+)?(%2[fF])?/g;
const LOCKFILES = new Set(['package-lock.json', 'npm-shrinkwrap.json']);
const SKIP = new Set(['.git', 'node_modules']);

function normalize(text) {
  return text.replace(PROXY, (_, scope, sep) => 'https://registry.npmjs.org/' + (scope ?? '') + (sep ? '/' : ''));
}

function* lockfiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory() && !SKIP.has(entry.name)) yield* lockfiles(join(dir, entry.name));
    else if (entry.isFile() && LOCKFILES.has(entry.name)) yield join(dir, entry.name);
  }
}

for (const file of lockfiles('.')) {
  const before = readFileSync(file, 'utf8');
  const after = normalize(before);
  if (after !== before) {
    writeFileSync(file, after);
    console.log('npm-lockfile-registry: rewrote proxy URLs in ' + file);
  }
}
