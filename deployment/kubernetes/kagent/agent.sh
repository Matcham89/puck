#!/bin/sh
# Usage: deployment/kubernetes/kagent/agent.sh   Applies the agent (ModelConfig, EgressPolicy, Agent) and waits until it is Ready.
# Rerun after editing kagent/agent.yaml. The agent's rules, cards and skills need no reload: the gate reads them from the
# knowledge repo's main on every job (catalog entry with "knowledge": true).
set -eu
cd "$(dirname "$0")/../../.."; CTX=kind-event-driven-ai
kubectl --context $CTX -n kagent delete configmap agent-knowledge --ignore-not-found >/dev/null   # left by earlier versions
kubectl --context $CTX apply -f deployment/kubernetes/kagent/mcp.yaml
kubectl --context $CTX apply -f deployment/kubernetes/kagent/agent.yaml
kubectl --context $CTX -n kagent wait --for=condition=Ready agent/sre-agent --timeout=10m
