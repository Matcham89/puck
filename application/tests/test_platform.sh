#!/bin/sh
# Usage: sh application/tests/test_platform.sh   Checks the platform helpers in deployment/kubernetes/lib.sh (run from the repo root).
. deployment/kubernetes/lib.sh
ok=0; check() { if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1 (got '$3', want '$2')"; ok=1; fi; }
check "macOS arm64"   kagent-darwin-arm64 "$(kagent_asset Darwin arm64)"
check "macOS Intel"   kagent-darwin-amd64 "$(kagent_asset Darwin x86_64)"
check "Linux amd64"   kagent-linux-amd64  "$(kagent_asset Linux x86_64)"
check "Linux arm64"   kagent-linux-arm64  "$(kagent_asset Linux aarch64)"
check "unsupported OS is refused"  "" "$(kagent_asset Windows x86_64 2>/dev/null)"
check "unsupported CPU is refused" "" "$(kagent_asset Linux riscv64 2>/dev/null)"
f=$(mktemp); printf 'hello' > "$f"
check "sha256 of a known string" 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824 "$(sha256_of "$f")"; rm -f "$f"
check "need passes for present tools" "" "$(need sh 2>&1)"
msg=$(need sh definitely-not-a-tool another-missing 2>&1); case "$msg" in *"definitely-not-a-tool another-missing"*) check "need names every missing tool" ok ok;; *) check "need names every missing tool" ok "$msg";; esac
exit $ok
