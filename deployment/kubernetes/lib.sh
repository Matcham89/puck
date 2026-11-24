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

# kagent_asset <uname -s> <uname -m>  ->  the release asset for this platform (kagent-darwin-arm64, kagent-linux-amd64, ...)
kagent_asset() {
  case "$1" in Darwin) _os=darwin;; Linux) _os=linux;; *) echo "unsupported OS '$1': kagent publishes macOS and Linux CLIs" >&2; return 1;; esac
  case "$2" in arm64|aarch64) _arch=arm64;; x86_64|amd64) _arch=amd64;; *) echo "unsupported CPU '$2': kagent publishes arm64 and amd64 CLIs" >&2; return 1;; esac
  echo "kagent-$_os-$_arch"
}

# sha256_of <file>  ->  hex digest (sha256sum on Linux, shasum on macOS)
sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# need <tool>...  ->  stop with ONE clear message naming everything that is missing
need() { _miss=""; for _t in "$@"; do command -v "$_t" >/dev/null 2>&1 || _miss="$_miss $_t"; done
  [ -z "$_miss" ] || { echo "missing tools:$_miss. Install them first (see docs/getting-started.md, Prerequisites)." >&2; return 1; }; }
