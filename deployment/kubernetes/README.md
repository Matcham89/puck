# Deploying puck's services

This covers **puck's own services only**. The platform underneath is installed from its own documentation, not re-described here.

| Platform piece | Install from | What puck supplies |
|---|---|---|
| kagent (agent runtime) | [kagent installation docs](https://kagent.dev/docs/kagent/introduction/installation) | `kagent/values.yaml` (our Helm values) |
| Agent Substrate (gVisor sandbox + egress) | the Agent Substrate section of the kagent installation docs | nothing |
| Agent Gateway (holds the Anthropic key) | its Helm chart, pinned in `agentgateway/install.sh` | `agentgateway/values.yaml`, `agentgateway/netpol.yaml` |

Note: the kagent docs page I checked is the 0.10.x version; this repo pins kagent 1.0.1 (`cluster/install.sh`). Confirm commands against the 1.0 docs.

## What puck deploys

| Dir | Contents |
|---|---|
| `app/` | namespace `eda`: `redis`, `gate`, `intake`, `worker` (and `jira-sim`, a dev-only Jira stand-in), plus the target `catalog.json` |
| `kagent/agent.yaml` | the agent: `ModelConfig`, `EgressPolicy`, `Agent` in namespace `kagent` |
| `agent/` (repo root) | the agent's rules, cards and skills. `kagent/agent.sh` packs them into the `agent-knowledge` ConfigMap, and the Agent's prompt includes it (kagent `promptTemplate` data sources). Merging a learning PR changes what new jobs know after the next `agent.sh` (or your GitOps sync); running sessions keep their revision. Native kagent skills are not used yet: per kagent's docs a `git` skill source leaves the agent not Ready, and an OCI skill needs an HTTPS registry the runtime trusts (a local plain-HTTP kind registry does not work) |

## Order and criteria
Each step needs the one before it to be ready.

1. **Platform ready.** kagent (CRDs and controller), Agent Substrate, and Agent Gateway serving `http://agentgateway.agentgateway.svc.cluster.local` with the `event-driven-ai/` Anthropic route. The gateway's NetworkPolicy (`agentgateway/netpol.yaml`) must allow only Substrate's `ate-system` namespace, because the gateway has no auth of its own.
2. **Namespace** `eda` (`app/00-namespace.yaml`, pod security `restricted`).
3. **Secrets in `eda`**, created out of band and never committed:

   | Secret | Key | Mounted by | Meaning |
   |---|---|---|---|
   | `git-token` | `token` | gate only | fine-grained PAT, contents + pull requests write on the catalog's repos only |
   | `publish-key` | `key` | gate and worker | shared key; only the worker may call `/publish` |
   | `jira-webhook` | `secret` | intake | HMAC secret for `/webhooks/jira`; without it the endpoint returns 503 |
4. **ConfigMap `eda-catalog`** with key `catalog.json`: the repos the bot may work in (`app/catalog.example.json` shows the shape).
5. **Services image** `eda-services` built from `application/Dockerfile`. The manifests reference `localhost:5001/eda-services:dev`; for any real cluster pin a registry reference by tag or digest instead.
6. **Workloads, in this order:** `redis`, then `gate` (needs git-token and publish-key), then `intake` and `worker` (need redis, the catalog and the gate). Skip `jira-sim` outside development and point the worker's `JIRA_BASE_URL` at real Jira (see the comment in `app/worker.yaml` for its auth secret).
7. **Agent** `kagent/agent.yaml`, once the gateway and the gate Service exist: its egress is limited to the gateway and the gate, and it holds no credentials. Wait for `agent/sre-agent` to be Ready.
8. **Verify** with a dry run (`dry_run: true`): it should reach `validated-dry-run` with nothing pushed.

Security invariants to keep in any environment: the agent has no tokens; only the gate holds the git token; only the worker can publish; the agent's egress is the gateway and the gate; PRs are drafts and humans merge.

## Local kind (this repo's scripts)
`up.sh` runs, in order: `cluster/` (kind + Substrate), `kagent/install.sh`, `agentgateway/install.sh`, `app/install.sh`, `kagent/agent.sh`. Each is idempotent, so rerun one to update just that part. They deploy a **local kind cluster only**: the image is built and pushed to `localhost:5001`. `app/catalog.json` is yours and git-ignored (`sh setup.sh` writes it). `down.sh` removes the cluster; `job-cost.sh <job-id>` reports a job's LLM cost.

## Scaled deployment (Flux or Argo CD)
Not provided or tested here. The criteria above map onto it directly:
- **Ordering:** Flux `Kustomization.dependsOn`, or Argo CD sync waves, in the order of the list above (platform, namespace and secrets, catalog, workloads, agent).
- **Images:** reference the CI-built image (`ghcr.io/<owner>/<repo>`) by version tag or digest, never `:dev`.
- **Secrets:** from an external secret store or sealed secrets, never plaintext in git.
- **Catalog:** the ConfigMap, kept per environment.
