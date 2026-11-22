#!/bin/sh
# Usage: deployment/kubernetes/agentgateway/install.sh   Installs/updates the in-cluster Agent Gateway (standalone chart v1.7.0). Idempotent.
set -eu
cd "$(dirname "$0")/../../.."; CTX=kind-event-driven-ai
[ -s secrets/anthropic_api_key ] || { echo "put the Anthropic API key for the event-driven-ai route in secrets/anthropic_api_key" >&2; exit 1; }
k() { kubectl --context "$CTX" "$@"; }
k create namespace agentgateway --dry-run=client -o yaml | k apply -f -
k label namespace agentgateway pod-security.kubernetes.io/enforce=restricted --overwrite >/dev/null
umask 077; tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
tr -d '\n' < secrets/anthropic_api_key > "$tmp"           # a trailing newline would be sent as part of the key
k -n agentgateway create secret generic anthropic-api-key --from-file=api_key="$tmp" --dry-run=client -o yaml | k apply -f -
helm --kube-context "$CTX" upgrade --install agentgateway oci://ghcr.io/agentgateway/charts/agentgateway-standalone \
  --version 1.7.0 -n agentgateway -f deployment/kubernetes/agentgateway/values.yaml --wait --timeout 5m
k apply -f deployment/kubernetes/agentgateway/netpol.yaml
