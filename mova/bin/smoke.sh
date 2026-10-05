#!/usr/bin/env bash
# Run every Mova smoke and diff its stdout against the JVM golden. Exit 1 on any diff.
#   mova/bin/smoke.sh            all      mova/bin/smoke.sh kondo_smoke   one
set -uo pipefail
cd "$(dirname "$0")/../.."
S=mova/smoke; fail=0
run() { # name, command...
  local name=$1; shift
  [[ -n "${ONLY:-}" && "$name" != "$ONLY" ]] && return
  local golden; golden=$(ls $S/$name.jvm.golden.txt $S/$name.jvm.out 2>/dev/null | head -1)
  [[ -z "$golden" ]] && { echo "SKIP $name (no golden)"; return; }
  local err; err=$(mktemp)
  if diff -q <("$@" 2>"$err") "$golden" >/dev/null; then echo "OK   $name (stderr: $(wc -l <"$err" | tr -d ' ') lines)"; else echo "DIFF $name"; fail=1; fi
  rm -f "$err"
}
ONLY=${1:-}
run jsonrpc_echo python3 $S/drive_jsonrpc.py -- mova/bin/lsp-mova $S/jsonrpc_echo.clj
run lsp4clj_echo python3 $S/drive_jsonrpc.py --protocol lsp4clj -- mova/bin/lsp-mova $S/lsp4clj_echo.clj
for f in $S/*_smoke.clj; do n=$(basename "$f" .clj); run "$n" mova/bin/lsp-mova "$f"; done
run cli_smoke $S/cli_smoke.sh
run lsp_session python3 $S/lsp_client.py --server-cmd "mova/bin/clojure-lsp" --timeout 30
exit $fail
