# Personal agent plan: Linear in, reviewed draft PRs out

Status: proposal. Goal: use puck as a personal ticket-workload tool that works the same at any company or on side projects. File tickets in Linear, let agents work as many as I like in parallel, review the draft PRs, and widen what the agent may do only as the evidence earns it.

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
| 1 | Linear adapter: poll, allowlist, write-back (**status: written and unit-tested against a stub; the live Linear API is unverified**: the `commentCreate` and `issueUpdate` mutations and the un-prefixed `Authorization` header for personal keys have never run against Linear) | a ticket I create in Linear ends as a draft PR and a comment, with no manual submit |
| 2 | Router and the first extra domain (Flux) | the same eval suite passes per domain and a Flux ticket never loads the Terraform pack |
| 3 | Burn-in on real tickets in my own projects | ~20 tickets reviewed; each repeated mistake turned into a gate or eval case; weekly sample of outputs |
| 4 | Merge eligibility for the narrow classes | a month of sampled merges with no revert |
| 5 | Head chef for tickets that span domains | a real ticket needed two specialists; subtask ordering and PR strategy decided from that case |
| 6 | Always-on host (small VM or k3s) | agents work while I am away; alerting if the queue stalls |

## Open questions
- Which repos and companies first, and who owns the tokens for each.
- Linear free plan: 2 teams and a 250-issue cap, so archive finished issues; check the current limits on the pricing page (the page I read may be out of date). API and webhooks are included, API keys are rate limited to about 2,500 requests per hour.
- Hosting: laptop kind cluster for now; when to move it, and what that costs.
- GitHub App versus fine-grained tokens, and when.

## Sources
- Linear webhooks and signing: https://linear.app/developers/webhooks
- Linear API and webhooks on all plans, plan limits: https://linear.app/docs/api-and-webhooks, https://linear.app/pricing
