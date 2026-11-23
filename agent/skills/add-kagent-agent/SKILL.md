---
name: add-kagent-agent
description: Use when asked to add a new kagent Agent (kind Agent) to a Flux/GitOps repository, especially a read-only investigator agent.
---
1. Find the sibling agents first (`rg -ln 'kind: Agent'`). Copy the closest one exactly: apiVersion, runtime, modelConfig, tools block, a2aConfig skills. Change only name, description, prompt, tools and skills.
2. Before using any ModelConfig, MCP server or tool name, grep the repo to confirm it exists (`rg -n 'kind: ModelConfig'`, `rg -n 'kind: RemoteMCPServer|MCPServer'`, tool names in sibling agents). Never invent names.
3. Register the new file in the directory's `kustomization.yaml`, in the same style and order as the existing entries.
4. Read-only agent rules:
   - Tools: only read tools (get, describe, logs, events). No create, delete, patch, apply, scale or exec tools.
   - Scope it to the named namespaces only; no cluster-wide wording.
   - The prompt states: never print Secret contents, never exec into pods.
5. Write `PR.md`: title, where the file went and why, then two lists: "Verified" (names grepped, kustomization entry, YAML sibling parity) and "Not verified" (live cluster state, that the tools or model respond at runtime).
6. Submit; do not commit, push or open a PR.
