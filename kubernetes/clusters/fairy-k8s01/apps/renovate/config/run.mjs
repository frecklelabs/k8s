// Mints a GitHub App installation token for the "Freckle Renovate" App's
// frecklelabs installation, then runs Renovate with it. Renovate can't mint
// tokens from an App key itself, and a token lasts an hour, so it is minted
// fresh at the start of every run.
//
// Env: GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY (Secret renovate-github-app).
import { spawnSync } from 'node:child_process';
import { createSign } from 'node:crypto';

const ORG = 'frecklelabs';
const API = 'https://api.github.com';
const { GITHUB_APP_ID: appId, GITHUB_APP_PRIVATE_KEY: key, ...env } = process.env;
if (!appId || !key) {
  console.error('GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are required');
  process.exit(2);
}

function appJwt() {
  const now = Math.floor(Date.now() / 1000);
  const enc = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  const body = enc({ alg: 'RS256', typ: 'JWT' }) + '.' + enc({ iat: now - 60, exp: now + 540, iss: appId });
  return body + '.' + createSign('RSA-SHA256').update(body).sign(key, 'base64url');
}

// Thrown when GitHub says to back off. These two calls are the App's own
// budget, separate from the broker's, and the run has nowhere durable to keep
// a hold (no API access, /tmp is an emptyDir), so the run is skipped instead:
// the next slot is 30 minutes away and costs at most two calls (C-2211).
class RateLimited extends Error {}

async function github(method, path) {
  const res = await fetch(API + path, {
    method,
    headers: {
      Authorization: 'Bearer ' + appJwt(),
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
  });
  if (res.ok) return res.json();
  const h = (name) => res.headers.get(name);
  const retryAfter = Number(h('retry-after'));
  const remaining = h('x-ratelimit-remaining');
  const reset = Number(h('x-ratelimit-reset'));
  // GitHub signals both primary and secondary limits with 403 or 429; a 403
  // without these headers is a real permission error and still fails the run.
  if ((res.status === 403 || res.status === 429) && (retryAfter > 0 || remaining === '0' || res.status === 429)) {
    const until =
      retryAfter > 0 ? Date.now() + retryAfter * 1000 : reset > 0 ? reset * 1000 : Date.now() + 60_000;
    throw new RateLimited(
      method + ' ' + path + ': HTTP ' + res.status +
        ' retry_after=' + (h('retry-after') ?? '-') +
        ' remaining=' + (remaining ?? '-') +
        ' reset=' + (h('x-ratelimit-reset') ?? '-') +
        ' resource=' + (h('x-ratelimit-resource') ?? '-') +
        ' until=' + new Date(until).toISOString(),
    );
  }
  throw new Error(method + ' ' + path + ': HTTP ' + res.status);
}

// Renovate hands child processes (go mod tidy, npm and uv locks) only a
// fixed set of env plus customEnvVariables, so pass the package-proxy
// settings from cronjob.yaml along explicitly.
const PROXY_ENV = ['GOPROXY', 'GOSUMDB', 'GOPRIVATE', 'NPM_CONFIG_REGISTRY', 'PIP_INDEX_URL', 'UV_INDEX_URL'];
const proxyEnv = Object.fromEntries(PROXY_ENV.filter((k) => env[k]).map((k) => [k, env[k]]));

let inst, token;
try {
  inst = await github('GET', '/orgs/' + ORG + '/installation');
  ({ token } = await github('POST', '/app/installations/' + inst.id + '/access_tokens'));
} catch (err) {
  if (!(err instanceof RateLimited)) throw err;
  // Exit 0: a back-off is expected, not a failed Job (KubeJobFailed).
  console.log('renovate: INFO GitHub rate limit, skipping this run: ' + err.message);
  process.exit(0);
}
console.log('renovate: running for ' + ORG + ' (installation ' + inst.id + ')');
const run = spawnSync('renovate', [], {
  stdio: 'inherit',
  env: {
    ...env,
    RENOVATE_TOKEN: token,
    // Never put the token in here: Renovate logs customEnvVariables
    // unmasked at debug level (C-9 dry run, 2026-11-07).
    RENOVATE_CUSTOM_ENV_VARIABLES: JSON.stringify(proxyEnv),
  },
});
process.exit(run.status ?? 1);
