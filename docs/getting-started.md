# Getting started

**puck** is the bot (this repo). It works on two kinds of repo: **puck itself**, for controlled self-learning (draft PRs touching only `agent/skills/` and `agent/cards/`), and the **target repos** in your Org/User, where it does the real work. Both are entries in one catalog; see the table in the README.

Two paths: A) CI for the image, B) deploy and run it against your target repos.


## A. CI pipeline (`.github/workflows/ci.yml`)

On every pull request it runs **lint** (shellcheck, Python compile, JSON, YAML) and **test** (builds the image, runs all unit tests inside it). On pushes to `main` and `v*` tags it also **builds a multi-arch image (amd64 + arm64) and pushes it**.

### Zero-config setup (GitHub Container Registry)
1. Fork or copy this repo to GitHub. The workflow names the image after the repo (`ghcr.io/<owner>/<repo>`), so nothing is hard-coded.
2. Settings > Actions > General > Workflow permissions: allow **read and write** (the push job asks for `packages: write` using the built-in `GITHUB_TOKEN`; no secret to create).
3. Push to `main`. The image appears under the repo's Packages tab. Tags: `sha-<commit>`, `latest` (default branch), and the tag name for `v*` tags.

### Pushing somewhere else (Docker Hub, ECR, Artifactory, ...)
Set these in Settings > Secrets and variables > Actions. All are optional; unset means GHCR.

| Name | Kind | Meaning |
|---|---|---|
| `REGISTRY` | variable | registry host, e.g. `docker.io`, `123456789.dkr.ecr.eu-west-1.amazonaws.com` |
| `IMAGE_NAME` | variable | image path, e.g. `myorg/puck` (default: the repository name, lowercased) |
| `REGISTRY_USERNAME` | secret | login user (default: the GitHub actor) |
| `REGISTRY_TOKEN` | secret | login token or password (default: `GITHUB_TOKEN`) |

Use a token scoped to push to this one image only, and rotate it on a schedule.

### Releases (release PR, from commit messages)
Releases use [release-please](https://github.com/googleapis/release-please-action). On every push to `main` (after lint and test) it reads the Conventional Commits since the last release and keeps one **release PR** open: it records the version in `.release-please-manifest.json` and updates `CHANGELOG.md`. **Merging that PR** creates the tag and the GitHub Release, and the same pipeline run pushes the image with that version tag (for example `v1.2.0`) next to `sha-<commit>` and `latest`.

| Commit / PR title | Release |
|---|---|
| `feat: ...` | minor (`1.2.0` -> `1.3.0`) |
| `fix: ...` | patch (`1.2.0` -> `1.2.1`) |
| `feat!: ...`, `fix(scope)!: ...`, or a `BREAKING CHANGE:` footer | major (`1.2.0` -> `2.0.0`) |
| `docs:`, `chore:`, `ci:`, `build:`, `test:`, `refactor:`, `style:` | none; `docs` and `chore` still appear in the changelog |

- **Pre-GA:** the first release is pinned to `v0.1.0` (`initial-version`), and while the version is below 1.0 a breaking change bumps the **minor** version, not major (`bump-minor-pre-major`). Nothing is released until a `feat` or `fix` lands on `main`. To cut `v0.1.0` immediately, push a commit with the footer `Release-As: 0.1.0`.
- **Going GA:** remove `initial-version` and `bump-minor-pre-major` from `release-please-config.json`, then release `1.0.0` with a `Release-As: 1.0.0` commit footer; breaking changes bump major from then on.
- Squash merges use the **PR title** as the commit message, so PR titles must follow this format; the `pr-title` check enforces it. The agent's own `learn/*` draft PRs are exempt.
- **One-time setting:** Settings > Actions > General > Workflow permissions > tick **Allow GitHub Actions to create and approve pull requests**.
- The release PR and tag are created by `github-actions[bot]`, so it appears as a contributor. PRs and tags made with the default token do not trigger other workflows, so CI will not run on the release PR itself. To make it run, add a PAT (contents + pull requests write) as the `RELEASE_PLEASE_TOKEN` secret; the workflow uses it when present.

### Gates
Pull requests never push. The push job needs `lint` and `test` to pass first. Fork PRs get no secrets, so they can lint and test but not push.

## B. Run it locally

### Prerequisites
[OrbStack](https://orbstack.dev) (or Docker Desktop/Colima) with at least ~5.5 GB memory and 15 GB free disk, plus `kubectl`, `helm`, `go`, `gh` and `git`. Apple Silicon or x86 both work (the image is built per architecture).

### 1. Secrets and config (one command)
`sh setup.sh` creates every file below plus the catalog; `sh setup.sh --check` shows what is missing. The tables explain each file.

#### Secrets
All under `secrets/` (git-ignored; keep mode 600):

| File | What |
|---|---|
| `git_token` | fine-grained GitHub PAT: **contents: write** and **pull requests: write** on the **target** repo(s) **and on puck** (self-learning), shortest expiry you tolerate; enable branch protection so the bot cannot merge |
| `anthropic_api_key` | Anthropic key; held by the in-cluster gateway, never by the agent |
| `publish_key`, `jira_webhook_secret` | generated by `setup.sh`; you only share `jira_webhook_secret` with Jira |

### 2. Declare the repos the bot may work in
```sh
sh setup.sh        # prompts for the repos and tokens, writes deployment/kubernetes/app/catalog.json and secrets/*
```
`setup.sh` writes a `puck` entry (self-learning, your fork) plus one entry per target repo; edit the JSON for per-repo detail. Fields: `repo` (`OWNER/REPO`), `base_branch`, `allowed_paths` (the only files the agent may change), `owner`, `lint` checks (`json`, `yaml`, `shell`, `helm`, `kubernetes`, `terraform`, `tflint`, or `playbook` for plugin repos), and an optional `jira` rule. `app/install.sh` loads this file into the cluster; there is no second copy to keep in sync. The `git_token` PAT must cover exactly these repos.

### 3. Bring it up (local kind)
```sh
sh deployment/kubernetes/up.sh      # kind + Agent Substrate + kagent + gateway + services; safe to rerun
```
Rebuild and redeploy after a code or catalog change: `sh deployment/kubernetes/app/install.sh`.

### 4. Send a change request (dry run: validates, pushes nothing)
```sh
kubectl --context kind-event-driven-ai -n eda port-forward svc/intake 8080:8080 &
curl -s localhost:8080/events -d '{"service":"<catalog key>","instruction":"Describe the change you want, at least ten characters","dry_run":true}'
```
Open http://localhost:8080 for the queue, stage chips and event log. Drop `dry_run` to get a draft PR on branch `learn/<ticket>`.

Events from `/events` and the UI are recorded as **unattributed**. Jira events are attributed to the person who added the trigger label (see below).

### 5. Jira (optional)
Point a Jira Cloud webhook at `/webhooks/jira` with the secret from `secrets/jira_webhook_secret`, then add the label `ai-agent` to a ticket (`ai-dry-run` too for a dry run). Try it without Jira: `python3 application/sim/jira.py create SRE-201 "Summary" "Description" --labels ai-agent,ai-dry-run`. For real Jira write-back set `JIRA_BASE_URL` on the worker and create the `jira-api` secret (see the comment in `eda.yaml`).

## Who the agent acts as
The agent keeps its own identity (`agent:event-driven-ai`) and is recorded as acting **on behalf of** the requester. It never uses the requester's credentials. The subject comes only from the signature-verified Jira webhook; callers of `/events` cannot name one. You see it in the job event log, the commit trailer `On-Behalf-Of:`, the draft PR footer and the Jira comment. This is attribution, not a delegated token; if a downstream service must verify delegation, add RFC 8693 token exchange.

## Tests
- Unit, in the image: `docker build -f application/Dockerfile -t eda-services:test . && sh application/tests/run.sh`
- End to end (needs a running cluster; two small LLM jobs, dry runs only): `python3 tests/e2e.py`

## Troubleshooting
- `up.sh` fails at the kagent or gateway install with `403 denied` from ghcr.io (the kagent CLI prints only "failed to run helm"): Helm 4 sends stored registry credentials (macOS keychain / gcloud helper) on anonymous pulls, and ghcr rejects stale ones. The install scripts already run Helm with no credential helpers (`deployment/kubernetes/lib.sh`); if you run `helm` yourself against these charts, use the same clean `PATH`, or `docker logout ghcr.io`.
- Build fails with `unsupported TARGETARCH`: use `docker build` (BuildKit) or `buildx`, not a legacy builder.
- `up.sh` refuses to start: it checks free disk, VM memory and that both secret files are non-empty.
- Image pull errors in the cluster: `app/install.sh` pushes to the kind registry at `localhost:5001`; rerun `up.sh` to recreate it.

For a real cluster, install the platform from the kagent docs and deploy puck's services with Flux or Argo CD: see `deployment/kubernetes/README.md` (order, criteria, secrets).
