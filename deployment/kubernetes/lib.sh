# shellcheck shell=sh disable=SC2034
# Sourced by the install scripts.
# Helm 4 sends stored registry credentials (docker-credential-osxkeychain, -gcloud, ...) even on anonymous pulls, and ghcr.io answers
# 403 "denied" when they are stale. Every chart here is public, so run helm (and the kagent CLI, which calls it) with a PATH that has
# no credential helpers, an empty registry config, and an empty Docker config. Your stored credentials are never read or changed.
anon_env() {
  _d=$(mktemp -d "${TMPDIR:-/tmp}/puck-anon.XXXXXX"); mkdir "$_d/bin" "$_d/docker"; echo '{}' > "$_d/docker/config.json"; echo '{}' > "$_d/helm-registry.json"
  for _t in helm kubectl; do ln -s "$(command -v "$_t")" "$_d/bin/$_t"; done
  ANON_PATH="$_d/bin:/usr/bin:/bin:/usr/sbin:/sbin"; ANON_DOCKER_CONFIG="$_d/docker"; ANON_HELM_REGISTRY_CONFIG="$_d/helm-registry.json"
}
