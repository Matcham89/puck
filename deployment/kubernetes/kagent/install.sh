#!/bin/sh
# Usage: deployment/kubernetes/kagent/install.sh   Installs kagent (the agent runtime) with Helm via the pinned kagent CLI. Idempotent.
# The agent definition itself is kagent/agent.yaml, applied by kagent/agent.sh once the app and gateway exist.
set -eu
cd "$(dirname "$0")/../../.."; ROOT=$PWD; V=$ROOT/vendor
# Helm gets an empty registry config because the charts are public and a missing Docker credential helper makes Helm fail on the
# OCI pull. The gateway holds the real Anthropic key; this one is a placeholder.
mkdir -p /tmp/eda-helm && echo '{}' > /tmp/eda-helm/config.json
printf 'install:\n  kagent:\n    valuesFiles:\n      - %s/deployment/kubernetes/kagent/values.yaml\n' "$ROOT" > /tmp/eda-helm/kagent-config.yaml
HELM_REGISTRY_CONFIG=/tmp/eda-helm/config.json ANTHROPIC_API_KEY=placeholder-gateway-holds-the-real-key \
  "$V/bin/kagent" --config /tmp/eda-helm/kagent-config.yaml install --provider Anthropic --timeout 20m
