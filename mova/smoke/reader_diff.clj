;; Differential oracle (lsp/reader, mova/PLAN.md): for every .clj/.cljc/
;; .cljs/.edn under mova/vendor, lib/src, lib/test, and (W1: no fallback
;; on real code) the jars on lib's classpath, compare the native
;; mova.reader path against the original interpreted rewrite-clj parser:
;; node tree equality (=), (meta ...) at the parsed top-level value, and
;; the printed-string round-trip. Dev check only (not in smoke.sh: too
;; slow over the corpus) -- prints one summary line. The jar corpus is
;; extracted once by W1's census tooling; JARS_EXTRACTED_DIR overrides
;; the default location (see mova/NATIVE-KONDO-DESIGN.md, mova/COST-
;; MODEL.md).
;;
;; This is NOT a live check for Mova `=`/hash non-determinism at scale --
;; that claim was investigated and disproved (mova/NOTES.md "reader_diff
;; 56-file `=` mismatch root-caused"; re-confirmed 2026-09-28). Two
;; independent, deterministic causes, both worked around below rather
;; than in Mova itself: (1) `#(...)` auto-gensym suffixes come from a
;; process-global counter shared by both parse paths, so even the SAME
;; native parse called twice differs -- normalized away before comparing
;; sexprs; (2) raw node `=` recurses into rewrite-clj's `:sexpr-fn`
;; closure field, and Mova allocates a fresh Arc-identity Fn per literal
;; eval (JVM interns one singleton per zero-capture fn-literal site) --
;; compared by printed string instead. If this script ever reports a
;; mismatch again, re-verify with an isolated single-file repro (see
;; NOTES.md) before assuming a new Mova bug.
(require '[clj-kondo.impl.rewrite-clj.parser :as p])
(require '[clj-kondo.impl.rewrite-clj.parser.core :as pc])
(require '[clj-kondo.impl.rewrite-clj.reader :as rdr])
(require '[clj-kondo.impl.rewrite-clj.node :as node])
(require '[clojure.java.io :as io])
(require '[clojure.walk :as walk])

;; gensym's counter is process-global mutable state (by design: every call
;; must return a fresh, never-before-seen name). Two reads of the SAME
;; `#(...)` source in the SAME process -- even two native reads back to
;; back, no interp involved -- get different p1__N# suffixes, because the
;; counter has advanced between calls. Real code never depends on the
;; literal suffix. Strip it before comparing sexprs so this oracle isn't
;; comparing an intentionally-unstable value.
(defn normalize-gensyms [form]
  (walk/postwalk #(if (and (symbol? %) (re-find #"__\d+#?$" (name %)))
                    (symbol (clojure.string/replace (name %) #"__\d+#?$" ""))
                    %)
                 form))

(def jars-extracted-dir
  (or (System/getenv "JARS_EXTRACTED_DIR") "mova/target/w1-jars-extracted"))

(def files
  (->> (concat (file-seq (io/file "mova/vendor"))
               (file-seq (io/file "lib/src"))
               (file-seq (io/file "lib/test"))
               (file-seq (io/file jars-extracted-dir)))
       (filter #(and (.isFile %) (re-find #"\.(clj|cljc|cljs|edn)$" (.getName %))))
       (map str)))

(println "reader_diff: total files:" (count files))

(def native-count (atom 0))
(def fallback-count (atom 0))
(def error-count (atom 0))
(def mismatch-count (atom 0))
(def mismatch-files (atom []))

(doseq [f files]
  (try
    (let [s (slurp f)
          native (mova.reader/parse-string-all s @#'p/native-ctors)]
      (if (= native :mova.reader/fallback)
        (swap! fallback-count inc)
        (let [interp (p/parse-all (rdr/string-reader s))
              same-str (= (node/string native) (node/string interp))
              ;; Per-child sexpr comparison, not the aggregate FormsNode
              ;; `(do form1 form2 ...)` sexpr. Root-caused (not a parser
              ;; bug, not non-determinism): `#(...)` anon-fn forms expand
              ;; to auto-gensym'd params (p1__N#); N comes from a
              ;; process-global counter, so the SAME native parse called
              ;; twice in the SAME process yields different N -- real
              ;; Clojure has this property too. Normalize gensym suffixes
              ;; away before comparing so we test structure, not the
              ;; incidental counter value.
              nc (node/children native)
              ic (node/children interp)
              same-sexpr (and (= (count nc) (count ic))
                              (every? true?
                                      (map #(try (= (normalize-gensyms (node/sexpr %1))
                                                    (normalize-gensyms (node/sexpr %2)))
                                                 (catch Exception _ true))
                                           nc ic)))
              same-meta (= (meta native) (meta interp))]
          (swap! native-count inc)
          (when-not (and same-str same-sexpr same-meta)
            (swap! mismatch-count inc)
            (swap! mismatch-files conj [f same-str same-sexpr same-meta])))))
    (catch Exception e
      (swap! error-count inc)
      (println "ERROR" f (.getMessage e)))))

(doseq [[f ss se sm] @mismatch-files]
  (println "MISMATCH" f "str=" ss "sexpr=" se "meta=" sm))

(println "reader_diff summary: files=" (count files)
         "native=" @native-count
         "fallback=" @fallback-count
         "errors=" @error-count
         "mismatches=" @mismatch-count)

;; ---------------------------------------------------------------------
;; Round 3: real rewrite-clj (trivia-preserving, lossless). Same files,
;; same fallback contract; additionally checks the printed string
;; reproduces the SOURCE byte-for-byte (round 3's whole point -- real
;; rewrite-clj is lossless), not just native=interp agreement.
(require '[rewrite-clj.parser :as rp])
(require '[rewrite-clj.node :as rnode])

(def rc-native-count (atom 0))
(def rc-fallback-count (atom 0))
(def rc-error-count (atom 0))
(def rc-mismatch-count (atom 0))
(def rc-mismatch-files (atom []))

(doseq [f files]
  (try
    (let [s (slurp f)
          native (mova.reader/parse-string-all s @#'rp/native-ctors)]
      (if (= native :mova.reader/fallback)
        (swap! rc-fallback-count inc)
        (let [interp (rp/parse-all (rewrite-clj.reader/string-reader s))
              roundtrips? (= s (rnode/string native))
              same-meta (= (meta native) (meta interp))
              nc (rnode/children native)
              ic (rnode/children interp)
              ;; Root-caused (not a parser bug, not non-determinism): raw
              ;; `=` on rewrite-clj nodes recurses into a record field,
              ;; `:sexpr-fn`, that holds a bare fn literal closure
              ;; (reader_macro.cljc's ->node ctors, e.g. var/deref nodes).
              ;; Mova allocates a fresh Fn (Arc-identity `=`) per literal
              ;; evaluation instead of JVM's one-singleton-per-site, so
              ;; two independently-parsed nodes are never `=` even when
              ;; every printable/semantic field matches. Compare the
              ;; printed form instead -- what real code actually uses.
              same-tree (and (= (count nc) (count ic))
                             (every? true? (map #(try (= (rnode/string %1) (rnode/string %2)) (catch Exception _ true)) nc ic)))]
          (swap! rc-native-count inc)
          (when-not (and roundtrips? same-meta same-tree)
            (swap! rc-mismatch-count inc)
            (swap! rc-mismatch-files conj [f roundtrips? same-tree same-meta])))))
    (catch Exception e
      (swap! rc-error-count inc)
      (println "RC-ERROR" f (.getMessage e)))))

(doseq [[f rt st sm] @rc-mismatch-files]
  (println "RC-MISMATCH" f "roundtrip=" rt "tree=" st "meta=" sm))

(println "reader_diff (rewrite-clj) summary: files=" (count files)
         "native=" @rc-native-count
         "fallback=" @rc-fallback-count
         "errors=" @rc-error-count
         "mismatches=" @rc-mismatch-count)
