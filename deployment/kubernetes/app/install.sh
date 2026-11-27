#!/bin/sh
# Usage: deployment/kubernetes/app/install.sh   LOCAL KIND ONLY. Builds the services image, pushes it to the kind registry (localhost:5001),
# creates the secrets from ./secrets and the target catalog from app/catalog.json, then applies the manifests.
# Always targets the dedicated kind cluster explicitly. For a real cluster use GitOps (see ../README.md), not this script.
set -eu
cd "$(dirname "$0")/../../.."; C="--context kind-event-driven-ai"
IMG=localhost:5001/eda-services:dev
[ -f deployment/kubernetes/app/catalog.json ] || { echo "missing deployment/kubernetes/app/catalog.json (the repos the bot may work in): run 'sh setup.sh'" >&2; exit 1; }
docker build -q -f application/Dockerfile -t "$IMG" . && docker push -q "$IMG"
for f in deployment/kubernetes/app/*.yaml; do cat "$f"; echo ---; done | kubectl $C apply -f -   # yamls only, in order (kubectl would also pick up catalog.json)
kubectl $C -n eda create configmap eda-catalog --from-file=catalog.json=deployment/kubernetes/app/catalog.json --dry-run=client -o yaml | kubectl $C apply -f -
# One key per owner: secrets/git_token.<owner> -> key <owner>; plain secrets/git_token -> key "token" (fallback for any owner).
set --
for f in secrets/git_token secrets/git_token.*; do
  [ -s "$f" ] || continue; k=${f#secrets/git_token}; k=${k#.}; set -- "$@" "--from-file=$(printf '%s' "${k:-token}" | tr A-Z a-z)=$f"
done
[ $# -gt 0 ] || { echo "missing secrets/git_token.<owner>: run 'sh setup.sh'" >&2; exit 1; }
kubectl $C -n eda create secret generic git-token "$@" --dry-run=client -o yaml | kubectl $C apply -f -
[ -f secrets/publish_key ] || { umask 077; head -c 24 /dev/urandom | base64 > secrets/publish_key; }
[ -f secrets/jira_webhook_secret ] || { umask 077; head -c 24 /dev/urandom | base64 > secrets/jira_webhook_secret; }
kubectl $C -n eda create secret generic jira-webhook --from-file=secret=secrets/jira_webhook_secret --dry-run=client -o yaml | kubectl $C apply -f -
kubectl $C -n eda create secret generic publish-key --from-file=key=secrets/publish_key --dry-run=client -o yaml | kubectl $C apply -f -
[ -s secrets/linear_api_key ] && kubectl $C -n eda create secret generic linear-api --from-file=key=secrets/linear_api_key --dry-run=client -o yaml | kubectl $C apply -f -
# The services read the catalog at startup, so restart them to pick up a changed catalog or a rebuilt image.
kubectl $C -n eda rollout restart deploy/gate deploy/intake deploy/worker >/dev/null
kubectl $C -n eda rollout status deploy/gate deploy/intake deploy/worker --timeout=180s
