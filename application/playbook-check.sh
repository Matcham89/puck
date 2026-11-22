#!/bin/sh
# Usage: playbook-check.sh   The gate's `playbook` lint for plugin-marketplace repos: manifests, versions, skill frontmatter. Exit 0 = good.
# Run by the gate (application/eda.py) in a fresh clone of the target at its base branch with the agent's files applied.
# Path restrictions are NOT checked here: the catalog's allowed_paths are enforced by the gate before this runs.
set -eu
cd "${PLAYBOOK_DIR:-$PWD}"
V=${QUICK_VALIDATE:-/opt/quick_validate.py}; m=.claude-plugin/marketplace.json
git add -A
files=$(git diff --cached --name-only origin/main)
[ -n "$files" ] || { echo "no changes vs origin/main"; exit 1; }
jq empty "$m"
bad=$(jq -r '.plugins[]|select((.version|test("^[0-9]+\\.[0-9]+\\.[0-9]+$")|not) or ((.description|length)<10) or ((.source|type)!="string") or ((.category as $c|["development","productivity","security","testing","deployment","database","monitoring","design","learning"]|index($c)|not)) or (.name|test("^[a-z0-9]+(-[a-z0-9]+)*$")|not))|.name' "$m")
[ -z "$bad" ] || { echo "marketplace.json entries fail CI rules (semver/description/source/category/name): $bad"; exit 1; }
[ -z "$(jq -r '[.plugins[].name]|group_by(.)|map(select(length>1))|.[][0]' "$m")" ] || { echo "duplicate plugin names"; exit 1; }
for s in $(echo "$files" | grep '/SKILL\.md$' || true); do python3 "$V" "$(dirname "$s")"; done
for p in $(echo "$files" | sed -n 's|^plugins/\([^/]*\)/.*|\1|p' | sort -u); do
  pj=plugins/$p/.claude-plugin/plugin.json
  [ -f "$pj" ] || { echo "$p: missing $pj"; exit 1; }
  [ -f "plugins/$p/README.md" ] || { echo "$p: missing README.md"; exit 1; }
  [ "$(jq -r .name "$pj")" = "$p" ] || { echo "$p: plugin.json name must equal the directory name"; exit 1; }
  pv=$(jq -r .version "$pj"); mv=$(jq -r --arg p "$p" '.plugins[]|select(.name==$p).version' "$m")
  [ -n "$mv" ] || { echo "$p: no marketplace.json entry"; exit 1; }
  [ "$pv" = "$mv" ] || { echo "$p: plugin.json ($pv) and marketplace.json ($mv) versions differ"; exit 1; }
  old=$(git show "origin/main:$pj" 2>/dev/null | jq -r .version 2>/dev/null || true)
  [ -z "$old" ] || [ "$old" != "$pv" ] || { echo "$p: changed but version not bumped ($pv) in both manifests"; exit 1; }
done
echo "checks passed"
