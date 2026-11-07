#!/usr/bin/env bash
# Vendor the kubernetes-sigs/gateway-api standard-channel CRDs into
# kubernetes/apps/networking/gateway-api/.
#
# Run by Renovate (via postUpgradeTasks in .renovaterc.json5) when the TAG
# below is bumped, and by CI in --check mode on every PR to ensure the
# vendored content matches the pinned tag.
#
# To re-vendor manually:   ./scripts/vendor-gateway-api-crds.sh
# To check without writing: ./scripts/vendor-gateway-api-crds.sh --check
#
# Requires only git: upstream is public, so no token and no gh (the
# self-hosted Renovate image ships git but not gh).

set -euo pipefail

# renovate: datasource=github-releases depName=kubernetes-sigs/gateway-api
TAG=v1.6.3

REPO=kubernetes-sigs/gateway-api
UPSTREAM_PATH=config/crd/standard
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$(cd "${SCRIPT_DIR}/../kubernetes/apps/networking/gateway-api" && pwd)"

MODE=${1:-vendor}

case "${MODE}" in
  vendor|--check) ;;
  --help|-h)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "ERROR: unknown argument '${MODE}'. Use --check, --help, or no args." >&2
    exit 64
    ;;
esac

command -v git >/dev/null || { echo "ERROR: git required" >&2; exit 2; }

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

# Shallow, blobless, sparse clone: only the tag's CRD directory is fetched.
SRC="${WORK}/src"
GIT_TERMINAL_PROMPT=0 git -c advice.detachedHead=false clone --quiet \
  --depth 1 --branch "${TAG}" --filter=blob:none --sparse \
  "https://github.com/${REPO}.git" "${SRC}"
git -C "${SRC}" sparse-checkout set "${UPSTREAM_PATH}"

# Files (not subdirectories) directly in the CRD directory, in byte order so
# the kustomization.yaml order never depends on the locale.
mapfile -t UPSTREAM_FILES < <(
  git -C "${SRC}" ls-tree --full-tree HEAD "${UPSTREAM_PATH}/" \
    | awk -F '\t' '$1 ~ / blob / { n = $2; sub(/.*\//, "", n); print n }' \
    | LC_ALL=C sort
)
[[ ${#UPSTREAM_FILES[@]} -gt 0 ]] || { echo "ERROR: no files at ${REPO}/${UPSTREAM_PATH}@${TAG}" >&2; exit 1; }

# Copy each into the temp dir with a provenance header.
for f in "${UPSTREAM_FILES[@]}"; do
  {
    cat <<EOF
# Vendored from ${REPO} @ ${TAG}
# Source: https://github.com/${REPO}/blob/${TAG}/${UPSTREAM_PATH}/${f}
# To bump: bump TAG in scripts/vendor-gateway-api-crds.sh and re-run.
EOF
    cat "${SRC}/${UPSTREAM_PATH}/${f}"
  } > "${WORK}/${f}"
done

# Render expected kustomization.yaml so --check can compare it too.
{
  cat <<'EOF'
---
# yaml-language-server: $schema=https://www.schemastore.org/kustomization.json
# Vendored CRDs. To bump, change TAG in scripts/vendor-gateway-api-crds.sh
# and re-run (or let Renovate do it automatically on a tag bump).
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
EOF
  for f in "${UPSTREAM_FILES[@]}"; do
    echo "  - ./${f}"
  done
} > "${WORK}/kustomization.yaml"

if [[ "${MODE}" == "--check" ]]; then
  fail=0
  for f in "${UPSTREAM_FILES[@]}" kustomization.yaml; do
    if ! diff -q "${DEST_DIR}/${f}" "${WORK}/${f}" >/dev/null 2>&1; then
      echo "MISMATCH: ${f}"
      diff "${DEST_DIR}/${f}" "${WORK}/${f}" | head -20 || true
      fail=1
    fi
  done
  # Flag orphan vendored files that no longer exist upstream.
  while IFS= read -r local_file; do
    name=$(basename "${local_file}")
    [[ "${name}" == "kustomization.yaml" ]] && continue
    if ! printf '%s\n' "${UPSTREAM_FILES[@]}" | grep -qx "${name}"; then
      echo "ORPHAN (no longer in upstream): ${name}"
      fail=1
    fi
  done < <(find "${DEST_DIR}" -maxdepth 1 -name '*.yaml')
  if [[ ${fail} -eq 0 ]]; then
    echo "OK: vendored CRDs in ${DEST_DIR##*/kubernetes/} match ${REPO}@${TAG}"
  fi
  exit ${fail}
fi

# Vendor mode: wipe existing CRD YAMLs (keep kustomization.yaml for the
# moment), copy new ones, regenerate the kustomization.yaml.
echo "Vendoring ${REPO}@${TAG} -> ${DEST_DIR}"
find "${DEST_DIR}" -maxdepth 1 -name '*.yaml' ! -name 'kustomization.yaml' -delete
for f in "${UPSTREAM_FILES[@]}"; do
  cp "${WORK}/${f}" "${DEST_DIR}/${f}"
done
cp "${WORK}/kustomization.yaml" "${DEST_DIR}/kustomization.yaml"

echo "Done. ${#UPSTREAM_FILES[@]} files vendored."
