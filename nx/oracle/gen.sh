#!/bin/bash
# Usage: gen.sh <e2e-clj|e2e-bb|e2e-cljs|e2e|lib|lint|jars>. Goldens -> ~/.cache/nx-oracle/golden/<corpus>/
# Env: OUT=<dir> overrides output base (e.g. candidate dirs); WT=<worktree root>.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${WT:-$(cd "$HERE/../.." && pwd)}"
SCR="${SCR:-${TMPDIR:-/tmp}/nx-oracle}"
OUTBASE="${OUT:-$HOME/.cache/nx-oracle/golden}"
corpus="${1:?corpus}"

if [ "$corpus" = e2e ]; then for c in e2e-clj e2e-bb e2e-cljs; do "$0" $c; done; exit 0; fi

W="$SCR/work/$corpus"; OUTD="$OUTBASE/$corpus"
rm -rf "$W" "$OUTD"; mkdir -p "$W" "$OUTD"
PATHS="$W.paths"; JARS="$W.jars"; : > "$JARS"; : > "$PATHS"; MODE=project

split_cp() { # stdin: classpath string; dirs -> PATHS (under $W), jars -> JARS
  tr ':' '\n' | while read -r p; do
    case "$p" in *.jar) echo "$p" >> "$JARS";; /*) ;; *) [ -d "$W/$p" ] && echo "$W/$p" >> "$PATHS";; esac
  done
}

case "$corpus" in
  e2e-*)
    P="${corpus#e2e-}"; cp -R "$WT/mova/lsp-e2e/projects/$P/." "$W/"; mkdir -p "$W/.clj-kondo"
    if [ -f "$W/deps.edn" ]; then (cd "$W" && clojure -Spath) | split_cp; else echo "$W/src" > "$PATHS"; fi ;;
  lib)
    mkdir -p "$W/src" "$W/test" "$W/test-helper/src"; cp -R "$WT/lib/src/." "$W/src/"; cp -R "$WT/lib/test/." "$W/test/"; cp -R "$WT/test-helper/src/." "$W/test-helper/src/"; cp "$WT/lib/deps.edn" "$W/"
    cp -R "$WT/.clj-kondo" "$W/"; cp -R "$WT/.lsp" "$W/"
    printf '%s\n%s\n%s\n' "$W/src" "$W/test" "$W/test-helper/src" > "$PATHS"
    (cd "$WT/lib" && clojure -Spath -A:test) | tr ':' '\n' | grep '\.jar$' > "$JARS" ;;
  lint) cp -R "$HERE/corpus/lint/." "$W/"; echo "$W/src" > "$PATHS" ;;
  jars)
    MODE=jars; mkdir -p "$W/.clj-kondo"
    (cd "$WT/lib" && clojure -Spath -A:test) | tr ':' '\n' | grep '\.jar$' > "$PATHS" ;;
  *) echo "unknown corpus $corpus"; exit 1 ;;
esac
echo "corpus=$corpus paths=$(wc -l < "$PATHS") jars=$(wc -l < "$JARS")"
S=$(python3 -c 'import time;print(time.time())')
JARARG=""; [ "$MODE" = project ] && [ -s "$JARS" ] && JARARG="$JARS"
(cd "$HERE" && clojure -M -m nx.oracle "$MODE" "$W" "$OUTD" "$PATHS" $JARARG) | tail -1
E=$(python3 -c 'import time;print(time.time())')
python3 -c "print('wall_s_with_jvm_start=%.1f' % ($E-$S))"
echo "files_out=$(find "$OUTD" -name '*.json' | wc -l)"
