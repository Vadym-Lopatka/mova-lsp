#!/usr/bin/env bash
# Turn clojure-lsp's JVM classpath into a Mova module path.
# Jars are unzipped (sources only) into mova/vendor/<jar>/; directories are
# kept as-is. Writes mova/module-path.txt, overlay and shims first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOVA="$ROOT/mova"
VENDOR="$MOVA/vendor"
mkdir -p "$VENDOR"

# Jars Mova provides itself (clojure.core, spec, core.async) or never loads.
SKIP='/org/clojure/clojure/|/org/clojure/spec.alpha/|/org/clojure/core.specs.alpha/|/org/clojure/core.async/|/org/clojure/test.check/'

CP="$(cd "$ROOT/cli" && clojure -Spath)"
paths=("$MOVA/overlay" "$MOVA/shims")
IFS=':' read -ra entries <<< "$CP"
for e in "${entries[@]}"; do
  if [[ "$e" == *.jar ]]; then
    [[ "$e" =~ $SKIP ]] && continue
    name="$(basename "$e" .jar)"
    dest="$VENDOR/$name"
    if [[ ! -d "$dest" ]]; then
      mkdir -p "$dest"
      unzip -q -o "$e" '*.clj' '*.cljc' '*.edn' '*.json' '*.txt' -x 'META-INF/*' -d "$dest" 2>/dev/null || true
      unzip -q -o "$e" 'clj-kondo/*' 'clojure-lsp/*' -d "$dest" 2>/dev/null || true
    fi
    # Skip jars with no Clojure source at all (pure Java).
    if find "$dest" -name '*.clj*' -print -quit | grep -q .; then paths+=("$dest"); fi
  else
    [[ "$e" = /* ]] || e="$ROOT/cli/$e"
    [[ -d "$e" ]] && paths+=("$(cd "$e" && pwd)")
  fi
done
(IFS=':'; echo "${paths[*]}") > "$MOVA/module-path.txt"
echo "module path: ${#paths[@]} roots -> $MOVA/module-path.txt"
