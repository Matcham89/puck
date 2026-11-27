# puck

An event-driven SRE agent: a Jira label or `POST /events` becomes a validated, draft pull request. The agent has its own identity (`agent:event-driven-ai`) and acts on behalf of the person who triggered it; it never uses their credentials.

## Two kinds of repo (keep them apart)
| | **puck** (this repo): the bot | **Target repos**: the Org/User repos it works in |
|---|---|---|
| What | services, agent knowledge, deployment, pipeline | where the bot does the real work |
| Bot access | **self-learning only**: draft PRs limited to `agent/skills/` and `agent/cards/` | draft PRs limited to each entry's `allowed_paths` |
| Never reachable by the bot | `application/`, `deployment/`, `.github/`, `agent/AGENTS.md` (not in its allowed paths) | everything outside `allowed_paths` |
| Declared in | the `puck` entry of the catalog | the other entries of `deployment/kubernetes/app/catalog.json` (yours, git-ignored; `sh setup.sh` writes it) |
| Who merges | you: protect `main`, require review | the target's owners |

One fine-grained PAT (`secrets/git_token`) covers puck plus the targets, **contents and pull requests write, those repos only**. The bot opens draft PRs on `<TICKET>/<change>` branches and never merges, so learned skills reach the agent only after a human reviews them. Enable branch protection (required review, no bypass) on puck and the targets, because the token alone does not stop a merge.

```
agent/        the bot's own knowledge: AGENTS.md, skills/, cards/   (read-only to the agent; it learns only via reviewed draft PRs)
application/  services image: intake, worker, gate, jobs/logs UI, lint, Jira client + simulator, unit tests
deployment/
  kubernetes/ one dir per component, each with an install.sh: cluster/ (kind + Substrate), kagent/, agentgateway/, app/ (services + catalog); up.sh runs them all
tests/        end-to-end tests against a running cluster
docs/         architecture write-up
```

All scripts run from the repo root. Runtime state is git-ignored: `secrets/`, `vendor/`, `.env`.

## Event flow
`POST /events` or `POST /webhooks/jira` -> **intake** (validate against `catalog.json`, enqueue in Redis) -> **worker** (A2A to the kagent agent in a gVisor sandbox) -> **gate** (no-LLM lint) -> **publish** (branch `<TICKET>/<change>`, draft PR). Failures return to the agent as a structured handoff (Objective, Artifact, Evidence, Status, Blockers, Next Action), max 3 attempts.

New here? Read [docs/getting-started.md](docs/getting-started.md).

## Quick start
```sh
sh setup.sh                          # asks for your repos and tokens; writes secrets/* and the catalog
sh setup.sh --check                  # confirm every file a clean boot needs exists
sh deployment/kubernetes/up.sh       # local kind cluster
```

## Commands
| What | Command |
|---|---|
| Configure repos and secrets | `sh setup.sh` |
| Cluster from nothing | `sh deployment/kubernetes/up.sh` |
| Rebuild and redeploy the services | `sh deployment/kubernetes/app/install.sh` |
| Tear down | `sh deployment/kubernetes/down.sh` |
| Unit tests (in the image) | `docker build -f application/Dockerfile -t eda-services:test . && sh application/tests/run.sh` |
| End-to-end (dry runs, no PRs) | `python3 tests/e2e.py [intake isolation skill lint jira]` |
| Cost of a job | `sh deployment/kubernetes/job-cost.sh <job-id>` |
| Simulate Jira | `python3 application/sim/jira.py create SRE-201 "Summary" "Description" --labels ai-agent,ai-dry-run` |

## Pipeline
`.github/workflows/ci.yml` lints, tests, pushes a multi-arch image to GHCR with no extra secrets, and keeps a release PR (version + `CHANGELOG.md`) from Conventional Commits via release-please: `feat` minor, `fix` patch, `feat!` major, `docs`/`chore` none; merging the PR tags the release. See getting-started. Seams: build `application/Dockerfile` (context = repo root), run the unit tests in it, lint `deployment/`. CD: `deployment/kubernetes/apply.sh`. Agent behaviour changes ship from `agent/` alone.

## Secrets
`secrets/git_token` (fine-grained PAT on the target repos only), `secrets/anthropic_api_key` (held by the gateway), `secrets/publish_key` and `secrets/jira_webhook_secret` (generated on first apply).
