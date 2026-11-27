# Hardening changes (backported from the Tempo fork)

A record of what changed between this repo's earlier state and the Tempo fork (`puck-tempo`), and why. The Tempo-specific parts (its tracked `catalog.json`, one repo-specific Terraform rule, its eval targets) were left out or generalised. 211 unit checks pass.

## Agent knowledge (`agent/`)
| Change | Why |
|---|---|
| `skills/helm-chart-change`, `skills/terraform-change` | The two change types seen so far: version-bump rules, blast radius, what the gate can and cannot run. Both now point at the MCP docs tools. |
| `skills/write-skill`: skill-authoring rules, general-only lessons | A lesson must help on a different repo; size limits match what the gate loads. |
| `cards/self-learning.md` rewritten | Write a lesson only if it is general, seen in the run and not already documented; "a check would catch this" points at a gate instead of a reminder. |
| `cards/stuck.md` | The sandbox has no python or curl; failing twice means switch tools. |
| `AGENTS.md`: MCP tools rule | One or two calls, hosted servers never get ticket text. |

## Gate (`application/eda.py`, `lint.py`)
| Change | Why |
|---|---|
| Per-owner tokens (`secrets/git_token.<owner>`), whole-repo `allowed_paths: ["*"]`, `<TICKET>/<change>` branches | Real repos across owners; one ticket, one branch. |
| Protected paths (`.github/`, `.buildkite/`, `CODEOWNERS`, lint config); catalog `allow_protected` opts out | The agent must not edit what runs with the repo's secrets or judges its own work. |
| Guards on added/removed lines: check suppressions, removed test assertions, committed secrets, Terraform `external` data and local/remote-exec | Deterministic anti-cheating; a prompt is advice, a gate is not negotiable. |
| `lesson_check` for learning jobs: no ticket keys/job ids/other repo names, card <=15 lines, skill <=40 lines, `agent/` under 80% of the knowledge cap | Lessons stay general, short and bounded; the gate loads all of `agent/` into every run and silently truncates past the cap. |
| `no-change` outcome | A submission with no files (the agent declined or withdrew) is an answer for a human, not a failed publish. Also re-checked after a review revision. |
| `branch_taken` check before the push | A rerun reuses the branch name; the push was rejected as a bare 500. |
| `/diff/<job>` endpoint, `files` stored per job | The reviewer and the evals read the change. |
| `rollup()` GraphQL fallback in `ci_status` | Fine-grained tokens have no Checks permission, so CodeQL/Cycode were invisible and CI could read green while red. The rollup state and total are readable with the same token. |

## Worker (`application/worker.py`)
| Change | Why |
|---|---|
| CI watch and fix loop (`ci_loop`, `CI_ROUNDS=2`) | After the draft PR, wait for CI; on red a fresh session fixes the same branch with the failing logs; `CI-UNRELATED` exit; `blind` state when only hidden checks are red. |
| Independent reviewer (`review`, `reviewed`) | A second agent in a fresh context reads ticket, PR.md and diff. `block` sends the author back once, a second block fails the job; the verdict goes in the PR body; a broken reviewer never blocks but the PR says it was not run. |
| `post()` and `fetch()` keep the gate's error body | "HTTP Error 500" hid the real reason (rejected push, failed clone). |
| `LESSONS_PER_DAY` (5); lesson job wording | Bounded learning; "only if it helps on a different repo". |
| Stage is `publish` while publishing | A publish failure showed as failing at validate. |

## Deployment (`deployment/kubernetes/`)
| Change | Why |
|---|---|
| `kagent/mcp.yaml`: `terraform-registry`, `helm-charts` (in cluster, digest-pinned, unprivileged) and hosted `aws-docs`, `k8s-docs`; none needs a token | Facts the sandbox cannot get; the agent still holds no credentials. |
| `reviewer-agent` + `eda-docs` egress policy | Reviewer reaches only the docs servers, not the gate or the repo. |
| `worker` replicas 2, `REVIEW_A2A_URL` | Jobs are taken with `BRPOP`, so each goes to exactly one worker. |
| `agentgateway/values.yaml`: route for the bare `claude-sonnet-5` alias | The harness sometimes asks for it; no route meant a 404 that killed the job. |

## Evals (`application/evals/run.py`)
Four dry-run cases scored on outcomes, not wording: a doc edit, a request to create a CI file (gate refuses), a token in the change (never reaches the diff), an invented Terraform module (reviewer blocks or the agent declines). Set `EVAL_DOCS_SERVICE` (and optionally `EVAL_TF_SERVICE`). Run twice before trusting a pass; the agent is not deterministic.

## Operational lessons
- After `agentgateway/install.sh` the gateway pod must be restarted for new routes to apply.
- A fine-grained PAT is limited to selected repos: a new target repo must be added to the token (the gate's error now says so).
- The agent reads CI config and satisfies it before CI runs; to test the fix loop the rule has to live somewhere it cannot see (a repo variable).
- Unprompted lessons overfit (a one-repo number); hence the lesson gate, the daily cap and the reviewer rule.

## Not carried over
The fork's tracked `catalog.json` and its three target entries, the Terraform rule naming the Tempo infrastructure repo, and the Tempo-repo eval targets.
