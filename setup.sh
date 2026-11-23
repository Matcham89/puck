#!/bin/sh
# Usage: sh setup.sh      Interactive. Writes deployment/kubernetes/app/catalog.json (the repos the bot may work in) and secrets/*.
# Non-interactive: PUCK_REPO=owner/puck TARGETS="owner/a owner/b" ALLOWED_PATHS=docs/,README.md GIT_TOKEN=... ANTHROPIC_API_KEY=... sh setup.sh
# Re-running keeps existing secrets unless FORCE=1. Tokens are never printed.
# sh setup.sh --check   reports which of the files a clean boot needs are present (exit 1 if any is missing).
set -eu
cd "$(dirname "$0")"
if [ "${1:-}" = "--check" ]; then
  rc=0
  for f in secrets/git_token secrets/anthropic_api_key secrets/publish_key secrets/jira_webhook_secret deployment/kubernetes/app/catalog.json; do
    if [ -s "$f" ]; then echo "ok       $f"; else echo "MISSING  $f"; rc=1; fi
  done
  [ "$rc" = 0 ] || echo "run: sh setup.sh"
  exit $rc
fi
ask() { # ask VAR "prompt" default  -- keeps a value already in the environment
  eval "cur=\${$1:-}"; [ -n "$cur" ] && return
  printf '%s [%s]: ' "$2" "$3"; read -r v || v=""; v=${v:-$3}; eval "$1=\$v"
}
secret() { # secret FILE VAR "prompt"
  [ "${FORCE:-}" = 1 ] || [ ! -s "secrets/$1" ] || { echo "secrets/$1 exists, keeping it"; return; }
  eval "cur=\${$2:-}"
  if [ -z "$cur" ]; then printf '%s: ' "$3"; stty -echo 2>/dev/null || true; read -r cur || cur=""; stty echo 2>/dev/null || true; echo; fi
  [ -n "$cur" ] || { echo "secrets/$1 left empty ($3)" >&2; return; }
  (umask 077; printf '%s' "$cur" > "secrets/$1")
}

echo "puck setup: which repos may the bot work in?"
ask PUCK_REPO "Your fork of puck, for self-learning PRs (OWNER/puck, or 'none')" "none"
ask TARGETS "Target repos in your Org/User, space-separated (OWNER/REPO ...)" ""
ask ALLOWED_PATHS "Paths the bot may change in targets (comma-separated; end a directory with /)" "docs/,README.md"
ask BASE_BRANCH "Base branch of the targets" "main"
ask OWNER_TEAM "Owning team label" "my-team"

export PUCK_REPO TARGETS ALLOWED_PATHS BASE_BRANCH OWNER_TEAM   # the Python step reads them, including prompted defaults
python3 - <<PY
import json, os, re
ok = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
entry = lambda repo, paths: {"repo": repo, "base_branch": os.environ.get("BASE_BRANCH", "main"), "allowed_paths": paths,
        "owner": os.environ["OWNER_TEAM"], "lint": ["json", "yaml", "shell"], "autofix": []}
cat = {}
puck = os.environ["PUCK_REPO"]
if puck.lower() != "none":
    assert ok.match(puck), f"PUCK_REPO must look like OWNER/puck, got {puck!r}"
    cat["puck"] = entry(puck, ["agent/skills/", "agent/cards/"])      # self-learning: skills and cards only
    cat["puck"]["knowledge"] = True                                     # the gate reads agent/ from this repo's main on every job
paths = [p.strip() for p in os.environ["ALLOWED_PATHS"].split(",") if p.strip()]
for repo in os.environ["TARGETS"].split():
    assert ok.match(repo), f"target {repo!r} must look like OWNER/REPO"
    key = repo.split("/")[1].lower()
    assert key not in cat, f"duplicate service name {key!r}; edit the catalog by hand"
    cat[key] = entry(repo, paths)
assert cat, "no repos given: set PUCK_REPO and/or TARGETS"
json.dump(cat, open("deployment/kubernetes/app/catalog.json", "w"), indent=2); open("deployment/kubernetes/app/catalog.json", "a").write("\n")
print("wrote deployment/kubernetes/app/catalog.json:", ", ".join(f"{k} -> {v['repo']}" for k, v in cat.items()))
PY

mkdir -p secrets && chmod 700 secrets
secret git_token GIT_TOKEN "GitHub fine-grained token (contents + pull requests write, on the repos above only)"
secret anthropic_api_key ANTHROPIC_API_KEY "Anthropic API key"
for f in publish_key jira_webhook_secret; do   # generated, not asked for: internal keys
  [ -s "secrets/$f" ] || (umask 077; head -c 24 /dev/urandom | base64 > "secrets/$f")
done
echo "next: sh deployment/kubernetes/up.sh   (details: docs/getting-started.md)"
