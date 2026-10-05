#!/usr/bin/env bash
# Measures clj-kondo analysis speed in µs/source-byte: Mova vs JVM clj-kondo.
# Runs mova/smoke/kondo_bench.clj, which calls clj-kondo.core/run! with the
# same shape clojure-lsp uses (see mova/smoke/kondo_smoke.clj), over two
# fixed corpora: one ~28KB file and a ~248KB/20-file directory. Each corpus:
# 1 cold in-process run, then 3 more repeats (median = "warm").
#
# Usage: mova/bin/kondo-bench.sh
# Env:   MOVA_BIN     (default mova)
#        MOVA_JIT_BIN (optional JIT build, run with MOVA_JIT=1;
#                      no default; the JIT run is skipped without it)
set -uo pipefail
cd "$(dirname "$0")/../.."

SCRIPT=mova/smoke/kondo_bench.clj
MOVA_BIN="${MOVA_BIN:-mova}"
MOVA_JIT_BIN="${MOVA_JIT_BIN:-}"

echo "== JVM clj-kondo (clojure -M) =="
if command -v clojure >/dev/null 2>&1; then
  (cd cli && clojure -M "../$SCRIPT")
else
  echo "SKIP: clojure not found"
fi

echo
echo "== Mova (MOVA_BIN=$MOVA_BIN) =="
if command -v "$MOVA_BIN" >/dev/null 2>&1; then
  MOVA_BIN="$MOVA_BIN" mova/bin/lsp-mova "$SCRIPT"
else
  echo "SKIP: MOVA_BIN not executable: $MOVA_BIN"
fi

echo
echo "== Mova JIT (MOVA_JIT=1, MOVA_BIN=$MOVA_JIT_BIN) =="
if [[ -n "$MOVA_JIT_BIN" && -x "$MOVA_JIT_BIN" ]]; then
  MOVA_BIN="$MOVA_JIT_BIN" MOVA_JIT=1 mova/bin/lsp-mova "$SCRIPT"
else
  echo "SKIP: MOVA_JIT_BIN not executable: $MOVA_JIT_BIN"
fi
