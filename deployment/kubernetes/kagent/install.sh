#!/bin/sh
# Usage: deployment/kubernetes/kagent/install.sh   Installs kagent (the agent runtime) with Helm via the pinned kagent CLI. Idempotent.
# The agent definition itself is kagent/agent.yaml, applied by kagent/agent.sh once the app and gateway exist.
set -eu
cd "$(dirname "$0")/../../.."; ROOT=$PWD; V=$ROOT/vendor
# shellcheck source=../lib.sh
. "$ROOT/deployment/kubernetes/lib.sh"; anon_env     # public charts: no stored registry credentials (see lib.sh)
mkdir -p /tmp/eda-helm
printf 'install:\n  kagent:\n    valuesFiles:\n      - %s/deployment/kubernetes/kagent/values.yaml\n' "$ROOT" > /tmp/eda-helm/kagent-config.yaml
# The gateway holds the real Anthropic key; this one is a placeholder.
PATH=$ANON_PATH DOCKER_CONFIG=$ANON_DOCKER_CONFIG HELM_REGISTRY_CONFIG=$ANON_HELM_REGISTRY_CONFIG ANTHROPIC_API_KEY=placeholder-gateway-holds-the-real-key \
  "$V/bin/kagent" --config /tmp/eda-helm/kagent-config.yaml install --provider Anthropic --timeout 20m
