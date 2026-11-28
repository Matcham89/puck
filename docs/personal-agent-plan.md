# Personal agent plan: Linear in, reviewed draft PRs out

Status (2026-11-28): Phase 1 works end to end on a local kind cluster against live Linear and a real repo (`home-cluster`); now in the improve loop below. Goal: use puck as a personal ticket-workload tool that works the same at any company or on side projects. File tickets in Linear, let agents work as many as I like in parallel, review the draft PRs, and widen what the agent may do only as the evidence earns it.

## Where we are
**Working, verified live** (local kind cluster, Linear team CHR, target `Matcham89/home-cluster`, about 22 tickets over five rounds):
- A Linear issue (label `agent` + `repo:<service>`, Todo, created by the key's owner) is polled, picked up, moved to In Progress, worked, reviewed, published as a draft PR, and the issue gets a comment with the PR link and CI result and moves to In Review. The un-prefixed `Authorization` header for personal keys, `issueUpdate` and `commentCreate` are confirmed.
- Easy work matches what Renovate does (single-line chart and image bumps). Harder work (a new Flux app, wiring orphaned PodMonitors with correct `dependsOn`, a four-minor chart upgrade with a release-notes check, a consistency change across files) came out in the repo's own style, and the agent said what it had not verified.
- Guards held: injected instructions in ticket text were refused, a plaintext key in a ticket was not committed (an ExternalSecret was used and the leak is flagged), a protected CI path was left alone with a proposal instead, dry-run opened no PR, a missing `repo:` label got one warning comment.
- The independent reviewer sends the author back with the findings (up to three reviews); in the last run it caught a real chart/ExternalSecret Secret-name collision that the earlier single review let through.
- CI on `home-cluster` is `flate` (replaced the deprecated flux-local, whose test job had only been checking 5 Kustomizations); a PR's `Test` renders the whole tree.
- Observability: agentgateway keeps a request log with prompts (`llm: full`), the kagent UI is on, the substrate worker pool runs 4 workers. Web UI is jobs and logs only, with a repo filter.

**Merged:** #11 adapter, #12 start state by name, #13 jobs-and-logs UI, #15 gateway logging + 4 substrate workers, #16 review loop on concerns, #18 three review rounds + reachable restore URL + Backlog parking. **Open:** the round F hardening branch (below).

**Known gaps** (each becomes a gate, an eval case or a fix, not more prompt text):
- **Wrong Helm values type.** Built (`helm-values` lint, `lint.type_diffs`): a HelmRelease value whose type differs from the chart's own `helm show values` default is rejected. Limits: keys the chart leaves commented out or null have no default to compare, and a chart that cannot be fetched is skipped. The CHR-25 case that prompted it was a misdiagnosis: qdrant chart 1.18.2 does support `apiKey.valueFrom` (it reads the secret with `lookup` at render time), so a human should still check ordering (the Secret must exist before the HelmRelease renders).
- Vague tickets: the job wrapper now tells the agent to change no file and ask (`Question:` in PR.md); it is parked in Backlog. It is a prompt rule, so it has an eval case (`vague ticket`) rather than a hard gate.
- At most 2 jobs run at once (2 puck workers); no per-repo limit yet, so two jobs can touch the same files.
- The reviewer varies run to run (it missed the Secret collision once). More rounds help but are not deterministic: turn each real catch into an eval case.
- Not confirmed: one actor per job in the substrate (look at the kagent Actors page), that the agentgateway log database fills, `agctl standalone analytics`.
- Job events still say "acting on behalf of unattributed" and label the write-back "Jira"; both are cosmetic, and a persona replaces the attribution later.
- The GitHub token covers everything the owner has; the catalog is what limits it to one repo. Narrow it before more repos are added.
- Not built yet: webhooks (needs a tunnel; polling is the fallback), the chat agent that files tickets, the router and domain packs (Phase 2).

## The improve loop (what happens next, repeated several times)
1. **Clean up:** delete the last batch of test tickets, close their draft PRs, delete their branches.
2. **Create tickets:** a deliberate mix: easy, hard, ambiguous, hostile, and one that should fail.
3. **Watch the flow:** pickup, states, the job event log, each review round, CI, the comment back.
4. **Watch the changes:** read every diff and PR body, compare with what a human or Renovate would do.
5. **Check behaviour:** did it do what was expected, and say honestly what it did not check?
6. **Review, repair, loop:** every miss is repaired in the pipeline (gate, reviewer loop, adapter, handoffs, tests), never by hand-editing the output; redeploy and rerun the same ticket.

| Round | Tickets | What it showed | Repaired |
|---|---|---|---|
| A | CHR-5 to 9 (chart and image bumps) | end to end works; a stale draining worker pod ran a job with old secrets (403) | rollout checked before running tickets |
| B | CHR-10 to 16 (guards and edge cases) | guards held; vague ticket failed; no-change and dry-run tickets stuck In Progress | start state by name (#12); jobs-only UI (#13) |
| C | CHR-17 to 20 (harder tasks, new CI) | good diffs; the flux-local `Test` job checked only 5 items | CI moved to flate |
| D | CHR-21 to 23 (new app, secret, consistency) | reviewer concerns reached the PR but never the author | one revision round on concerns (#16) |
| E | CHR-24 to 26 (same three) | loop works; a real problem first seen in round 2 had no chance to be fixed; handoff used a name the sandbox cannot resolve | three rounds, full gate URL, Backlog parking (open PR) |
| E2 | CHR-25 rerun (qdrant API key) | all three rounds ran and it published (#218). But the author set the chart's `apiKey` to a `valueFrom` map, which the chart does not support (`helm template` renders the map as text and still creates its own Secret). The first run's round-2 reviewer blocked exactly this; this run's round-3 reviewer only flagged the placeholder secret ID. flate passes because it only proves the manifests render | open: see the first known gap |

### Round F: eleven trials (CHR-28 to CHR-51), what each repaired
| Trial | Found | Repaired in the pipeline |
|---|---|---|
| 1 | vague ticket got an invented PR | stop-and-ask rule in the job wrapper, eval case |
| 1 | values-type gap | `helm-values` lint (and the qdrant claim above corrected) |
| 2 | string value for a boolean key | the new lint rejected it; the agent deviated openly and said so |
| 3 | hostile CI/push-to-main ticket refused; two same-file tickets fine; yamllint's EOF-newline rule forced unrelated diff lines | `new-line-at-end-of-file` off |
| 4, 5 | nonexistent chart version and nonexistent target handled by asking; removal PR claimed file deletions the diff did not contain | uploads could not express deletions: `submit.sh` now sends `.eda/deleted`, the gate applies it (allowed and protected checks), a deletion-only change counts as a change |
| 5 | one-limit change reindented ~60 lines | `guard`: 6+ lines that differ only in indentation are rejected |
| 6 | old yamllint debt in a touched file blocked the change (and pushed the agent to reindent) | yamllint findings are reported only for lines the agent added or changed |
| 7, 8, 9 | minimal grafana diff, file rename, 23-of-23 HelmReleases, dry-run no-op, injection inside a code fence ignored, a Kustomization to a missing path left unwired and asked | none needed |
| 10 | a worker restart during the CI wait was reported as a 20-minute CI timeout one second in | a stopping worker returns `interrupted`; recovery resumes the CI wait (bounded by RECOVERIES) instead |
| 11 | Linear rewrote `example.com/tier:` as a markdown link and the agent read it; a draining pod kept polling Linear and ran a ticket with old code | `linear.unlink` restores autolinked text; the poller stops on SIGTERM |

## Principles it follows
- **Trust ladder.** Every PR reviewed by me, then sampled, then agent-merged for narrow classes with strong verification. Never skip a rung.
- **Verification before autonomy.** Gate, independent reviewer, CI fix loop and evals already exist; each step up the ladder needs them to catch more, not hope.
- **Environment over prompts.** A repeated mistake becomes a gate or an eval case, not more prose.
- **Deterministic where possible.** Routing, allowlists and eligibility are code; the model only does the judgment work.
- **Right tools for the job.** Specialists by domain, each with its own knowledge pack and tool set.

## Shape
```
Linear issue -> intake adapter -> queue -> router -> specialist agent -> gate -> reviewer -> draft PR -> CI loop
     ^                                                                                               |
     +------------------- comment with PR link, verdict and state <----------------------------------+
```

## Pieces to build
1. **Linear adapter** (`linear.py`, modelled on `jira.py`).
   - Start with **polling** (GraphQL, every ~60 s): issues with a label such as `agent` in the `Todo` state. No inbound endpoint, so it works behind NAT on a laptop.
   - Add **webhooks** later (needs a tunnel): verify the `Linear-Signature` header, a hex HMAC-SHA256 of the raw body with the webhook secret, and reject a `webhookTimestamp` older than 60 s.
   - **Allowlist:** only issues created by me (or assigned to me) are processed. Ticket text is untrusted data; in a shared workspace this check is what stops someone else steering the agent.
   - **Idempotency:** the Linear issue id is the job key; move the issue to In Progress on pickup so it is never taken twice.
   - **Write-back:** a comment with the PR link, the reviewer verdict, or the reason for no change; move to In Review.
2. **Ticket to repo mapping.** A `repo:<name>` label or the Linear project maps to a catalog entry. The catalog is the only place repos and tokens are declared, so a new company is a new entry plus a token, nothing else.
3. **Deterministic router.** Input: the catalog entry's declared domains, the paths and file types the ticket names, and labels like `domain:flux`. Output: a specialist, or the generalist when nothing matches. Domain knowledge lives in `agent/domains/<name>/`; the gate loads the shared base plus the chosen pack. A separate `Agent` only where the tool set differs (Terraform: registry + AWS docs; Flux: Kubernetes docs + Helm). Each domain gets its own eval cases and reviewer criteria. Flux is the first new pack.
4. **Concurrency.** N workers already pull from one queue. Add Linear priority to queue order and a per-repo limit (start at one job per repo) so two jobs do not edit the same files.
5. **Merge eligibility (rung 3).** A deterministic rule, never the model's opinion: docs-only or comment-only changes, CI green, reviewer `approve`, no guard warnings. Anything touching production infrastructure paths is never eligible. Start with the flag off.
6. **Credentials.** Fine-grained tokens limited to chosen repos work but must be edited for every new repo, and have no Checks permission. A GitHub App installed per owner fixes both and gives a proper bot identity; do that once more than a couple of repos are in play.
7. **Cost.** Read from the Agent Gateway dashboard; attempts, fix rounds and the daily lesson cap already bound a job.

## Phases and exit criteria
| Phase | What | Done when |
|---|---|---|
| 0 | Backport the hardening work to puck (this PR) | merged, tests green, `docs/hardening-changes.md` is the record |
| 1 | Linear adapter: poll, allowlist, write-back (**done and verified live**; hardening continues in the improve loop) | a ticket I create in Linear ends as a draft PR and a comment, with no manual submit |
| 2 | Router and the first extra domain (Flux) | the same eval suite passes per domain and a Flux ticket never loads the Terraform pack |
| 3 | Burn-in on real tickets in my own projects (**started**: ~22 test tickets, see the improve loop) | ~20 real tickets reviewed; each repeated mistake turned into a gate or eval case; weekly sample of outputs |
| 4 | Merge eligibility for the narrow classes | a month of sampled merges with no revert |
| 5 | Head chef for tickets that span domains | a real ticket needed two specialists; subtask ordering and PR strategy decided from that case |
| 6 | Always-on host (small VM or k3s) | agents work while I am away; alerting if the queue stalls |

## Open questions
- Which repos and companies first, and who owns the tokens for each. The current token is broader than needed (see known gaps).
- What the chat agent that files tickets looks like, and whether a webhook (tunnel) is worth it over polling.
- Linear free plan: 2 teams and a 250-issue cap, so archive finished issues; check the current limits on the pricing page (the page I read may be out of date). API and webhooks are included, API keys are rate limited to about 2,500 requests per hour.
- Hosting: laptop kind cluster for now; when to move it, and what that costs.
- GitHub App versus fine-grained tokens, and when.

## Sources
- Linear webhooks and signing: https://linear.app/developers/webhooks
- Linear API and webhooks on all plans, plan limits: https://linear.app/docs/api-and-webhooks, https://linear.app/pricing
