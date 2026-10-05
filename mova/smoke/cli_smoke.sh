#!/usr/bin/env bash
# CLI parity smoke on fresh copies of cli/integration-test/sample-test.
# Compares RESULTS, not hash-order-dependent text: exit codes, the formatted /
# cleaned file contents, and sorted diagnostics lines (progress line dropped).
#   mova/smoke/cli_smoke.sh             # Mova
#   LSP=jvm mova/smoke/cli_smoke.sh > mova/smoke/cli_smoke.jvm.out   # golden
set -uo pipefail
cd "$(dirname "$0")/../.."
REPO=$(pwd)
lsp() {
  if [[ "${LSP:-mova}" == jvm ]]; then (cd "$REPO/cli" && clojure -M -m clojure-lsp.main "$@")
  else "$REPO/mova/bin/clojure-lsp" "$@"; fi
}
fresh() { rm -rf "$1" && mkdir -p "$1" && cp -R cli/integration-test/sample-test/. "$1"/; }
dump() { (cd "$1" && find src -type f | LC_ALL=C sort | while read -r f; do echo "## $f"; cat "$f"; done); }
noprogress() { grep -v '^\[  0%\]' | sed $'s/\x1b\\[[0-9;]*m//g'; }
# Hash-set literals in message text (e.g. "Different aliases #{s string cstring str}")
# print in JVM hash order; iteration order of hash sets is unspecified, so sort
# each set's elements before comparing.
normsets() {
  python3 -c '
import re, sys
def repl(m):
    return "#{" + " ".join(sorted(m.group(1).split())) + "}"
for line in sys.stdin:
    sys.stdout.write(re.sub(r"#\{([^{}]*)\}", repl, line))
'
}

ROOT=$(mktemp -d)
for cmd in format clean-ns; do
  fresh "$ROOT"
  lsp $cmd --dry --project-root "$ROOT" >/dev/null 2>&1; echo "=== $cmd --dry exit: $?"
  lsp $cmd --project-root "$ROOT" >/dev/null 2>&1; echo "=== $cmd exit: $?"
  dump "$ROOT"
done
fresh "$ROOT"
out=$(lsp diagnostics --project-root "$ROOT" 2>&1); code=$?
echo "=== diagnostics exit: $code"
# Same-ns fixture pair: refer-all lands on either file depending on :parallel timing (JVM too).
echo "$out" | noprogress | normsets | sed "s#$ROOT#<root>#g" | grep -Ev '(completion|declaration)/b\.clj:.*\[refer-all\]' | LC_ALL=C sort
rm -rf "$ROOT"
