#!/bin/sh
# Usage: deployment/kubernetes/cluster/install.sh   Preflight + pinned tooling + the kind cluster with Agent Substrate. Idempotent.
# Needs: docker, kind, kubectl, helm, git, curl (all checked below). Run from anywhere; it works from the repo root.
set -eu
cd "$(dirname "$0")/../../.."; ROOT=$PWD; V=$ROOT/vendor
# shellcheck source=../lib.sh
. "$ROOT/deployment/kubernetes/lib.sh"
need docker kind kubectl helm git curl
SUBSTRATE_TAG=v1.0.0; KAGENT_TAG=v1.0.1

# Preflight: the failures that bit us were a full Docker disk, too little memory, and missing secrets.
free_kb=$(docker run --rm alpine df -Pk / | awk 'NR==2{print $4}'); mem=$(docker info --format '{{.MemTotal}}')
[ "$free_kb" -ge 15000000 ] || { echo "Docker disk has under 15 GB free; run 'docker system prune' first" >&2; exit 1; }
[ "$mem" -ge 5500000000 ] || { echo "Docker VM has under ~5.5 GB memory; raise it in Docker/OrbStack settings" >&2; exit 1; }
{ [ -s secrets/git_token ] || ls secrets/git_token.* >/dev/null 2>&1; } || { echo "missing secrets/git_token or secrets/git_token.<owner>: run 'sh setup.sh'" >&2; exit 1; }
[ -s secrets/anthropic_api_key ] || { echo "missing secrets/anthropic_api_key: run 'sh setup.sh'" >&2; exit 1; }

# Pinned tooling: Substrate's cluster script and the kagent CLI (checksum verified).
mkdir -p "$V/bin"
[ -d "$V/substrate" ] || git clone -q --depth 1 --branch "$SUBSTRATE_TAG" https://github.com/agent-substrate/substrate "$V/substrate"
if [ ! -x "$V/bin/kagent" ]; then
  asset=$(kagent_asset "$(uname -s)" "$(uname -m)")                     # the CLI for THIS platform; plain curl, no GitHub login needed
  base=https://github.com/kagent-dev/kagent/releases/download/$KAGENT_TAG
  curl -fsSL -o "$V/bin/$asset" "$base/$asset" && curl -fsSL -o "$V/bin/$asset.sha256" "$base/$asset.sha256" || { echo "could not download $asset ($base)" >&2; exit 1; }
  [ "$(cut -d' ' -f1 "$V/bin/$asset.sha256")" = "$(sha256_of "$V/bin/$asset")" ] || { rm -f "$V/bin/$asset"; echo "kagent CLI checksum mismatch" >&2; exit 1; }
  mv "$V/bin/$asset" "$V/bin/kagent"; chmod +x "$V/bin/kagent"; rm -f "$V/bin/$asset.sha256"
fi

# Cluster (skip if it exists). No CPU reservation: with one 4-CPU node it only starves the pods.
if ! docker inspect event-driven-ai-control-plane >/dev/null 2>&1; then
  (cd "$V/substrate" && KIND_CLUSTER_NAME=event-driven-ai ./hack/create-kind-cluster.sh)
fi
