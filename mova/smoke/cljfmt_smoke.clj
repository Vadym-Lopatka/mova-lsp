;; lsp/host (clojure-lsp-on-Mova campaign, mova/PLAN.md): stage 1 smoke for
;; `cljfmt.core/reformat-string`, the fn `clojure_lsp.feature.format/
;; formatting` calls to answer LSP `textDocument/formatting`. Exercises
;; the default `cljfmt/default-options` (real Clojure's own "no config
;; file" defaults) AND the exact shape `format.clj`'s `resolve-cljfmt-
;; config` passes -- `cljfmt.config/default-config` is `(merge cljfmt/
;; default-options {:project-root .. :paths .. :file-pattern .. :ansi? ..
;; :parallel? ..})` (the extra keys `reformat-string` never reads) with
;; `(update :alias-map not-empty)` layered on top (works around a cljfmt
;; bug where an empty, present `:alias-map` breaks ns-alias resolution).
;; Requiring `cljfmt.config` itself is deliberately avoided here: it pulls
;; in `cljfmt.report` -> `clojure.stacktrace` (not vendored, and not
;; needed -- `reformat-string` never touches either), so this builds the
;; identical opts map by hand from `cljfmt.core/default-options` instead.
;;
;; `clojure-lsp.diff` (`difflib`/`java-diff-utils`) is NOT exercised: it
;; is a separate file `format.clj` never requires, used only for the
;; unified-diff shown elsewhere in clojure-lsp's own CLI output, not for
;; formatting itself. No `mova.diff`/`similar`-crate shim needed for this
;; smoke.
;;
;;   JVM:  cd cli && clojure -M ../mova/smoke/cljfmt_smoke.clj > ../mova/smoke/cljfmt_smoke.jvm.out
;;   Mova: MOVA_BIN=... mova/bin/lsp-mova mova/smoke/cljfmt_smoke.clj
;; Outputs must be byte-identical.
(ns cljfmt-smoke
  (:require
   [cljfmt.core :as cljfmt]))

(defn line [& args]
  (prn (vec args)))

;; `cljfmt.config/default-config`'s formatting-relevant half, without
;; requiring that namespace (see header comment).
(def default-opts cljfmt/default-options)
(def lsp-opts (update cljfmt/default-options :alias-map not-empty))

;; ---------------------------------------------------------------------------
;; 1. Reindentation (`:indentation?`).
(def indent-src "(defn foo [x]\n(+ x 1))\n")
(line :indent-default (cljfmt/reformat-string indent-src default-opts))

;; 2. Trailing whitespace removal (`:remove-trailing-whitespace?`).
(def trailing-src "(defn foo [x]   \n  (+ x 1))\n")
(line :trailing-default (cljfmt/reformat-string trailing-src default-opts))

;; 3. Consecutive blank line collapsing (`:remove-consecutive-blank-lines?`).
(def blank-src "(defn foo [x]\n\n\n\n  (+ x 1))\n")
(line :blanklines-default (cljfmt/reformat-string blank-src default-opts))

;; 4. Missing whitespace is NOT inserted between a reader macro and a
;;    following token the way it is between two forms -- `(+1 2)` reads
;;    as a single symbol/number pair, not `(+ 1 2)`, so this is a no-op
;;    (`:insert-missing-whitespace?` only separates ADJACENT forms).
(def ws-src "(+1 2)\n")
(line :missing-ws-default (cljfmt/reformat-string ws-src default-opts))

;; 5. Surrounding whitespace inside brackets (`:remove-surrounding-whitespace?`).
(def surround-src "( foo bar )\n")
(line :surround-default (cljfmt/reformat-string surround-src default-opts))

;; 6. A well-formed `let` (binding-vector indentation) round-trips
;;    unchanged, using the exact opts shape clojure-lsp's `format.clj`
;;    passes (`:alias-map` forced to `nil` when empty).
(def multi-src "(let [a 1\n      b 2]\n  (+ a b))\n")
(line :multi-lsp (cljfmt/reformat-string multi-src lsp-opts))

;; 7. `wrap-normalize-newlines` -- the exact wrapper `format.clj` composes
;;    `reformat-string` with, normalizing CRLF to LF on the way in.
(def crlf-src "(defn foo [x]\r\n  (+ x 1))\r\n")
(line :crlf-normalized
      ((cljfmt/wrap-normalize-newlines #(cljfmt/reformat-string % default-opts)) crlf-src))
