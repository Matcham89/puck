#!/bin/sh
# Usage: deployment/kubernetes/kagent/agent.sh   Loads the agent's knowledge (agent/AGENTS.md, cards, skills) into a ConfigMap, applies the agent
# (ModelConfig, EgressPolicy, Agent) and waits until it is Ready. Rerun after editing agent/ or kagent/agent.yaml.
# The Agent's systemPrompt includes the ConfigMap through kagent's documented promptTemplate dataSources. Running sessions keep their
# revision; new jobs get the new knowledge.
set -eu
cd "$(dirname "$0")/../../.."; CTX=kind-event-driven-ai
k=$(mktemp); trap 'rm -f "$k"' EXIT
{
  cat agent/AGENTS.md
  printf '\n\n# Cards (already loaded: follow the one that matches your situation)\n'
  for f in agent/cards/*.md; do printf '\n## Card: %s\n' "$(basename "$f" .md)"; cat "$f"; done
  printf '\n\n# Skills (already loaded: follow the one whose description matches the task)\n'
  for f in agent/skills/*/SKILL.md; do printf '\n## Skill: %s\n' "$(basename "$(dirname "$f")")"; cat "$f"; done
} > "$k"
grep -q '{{' "$k" && { echo "agent/ must not contain '{{' (it would be parsed as a prompt template)" >&2; exit 1; }
kubectl --context $CTX -n kagent create configmap agent-knowledge --from-file=knowledge="$k" --dry-run=client -o yaml | kubectl --context $CTX apply -f -
kubectl --context $CTX apply -f deployment/kubernetes/kagent/agent.yaml
kubectl --context $CTX -n kagent wait --for=condition=Ready agent/sre-agent --timeout=10m
