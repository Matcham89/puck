#!/bin/sh
# Usage: deployment/kubernetes/kagent/agent.sh   Applies the agent (ModelConfig, EgressPolicy, Agent) and waits until it is Ready.
# Rerun after editing kagent/agent.yaml (for example the agent's prompt).
set -eu
cd "$(dirname "$0")/../../.."; CTX=kind-event-driven-ai
kubectl --context $CTX apply -f deployment/kubernetes/kagent/agent.yaml
kubectl --context $CTX -n kagent wait --for=condition=Ready agent/sre-agent --timeout=10m
