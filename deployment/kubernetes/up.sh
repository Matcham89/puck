#!/bin/sh
# One command from nothing: kind cluster + Agent Substrate, kagent, Agent Gateway, the app, then the agent. Idempotent (safe to rerun).
# Each component has its own dir and install script; run one alone to update just that part.
set -eu
cd "$(dirname "$0")/../.."; CTX=kind-event-driven-ai
sh deployment/kubernetes/cluster/install.sh        # kind + Substrate
sh deployment/kubernetes/kagent/install.sh         # agent runtime
sh deployment/kubernetes/agentgateway/install.sh   # holds the Anthropic key; only the Substrate egress may reach it
sh deployment/kubernetes/app/install.sh            # intake, worker, gate, redis, UI, secrets, target catalog
sh deployment/kubernetes/kagent/agent.sh           # the agent definition
echo "up. Send an event: kubectl --context $CTX -n eda port-forward svc/intake 18080:8080  then POST /events"
