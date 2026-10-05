;; mova/smoke/kondo_smoke.clj
;;
;; Runs clj-kondo.core/run! (the analysis engine clojure-lsp calls
;; directly, see lib/src/clojure_lsp/kondo.clj) over a fixed corpus of
;; real files, with the same :config/:analysis shape clojure-lsp's
;; `config-for-internal-paths` builds, and :cache false. Prints a
;; deterministic, sorted, relative-paths summary so the JVM oracle's
;; output and Mova's output can be diffed byte-for-byte.
;;
;; Oracle: `cd cli && clojure -M ../mova/smoke/kondo_smoke.clj`
;; Mova:   `MOVA_BIN=... mova/bin/lsp-mova mova/smoke/kondo_smoke.clj`
;;         (run from the clojure-lsp-kondo repo root)
(ns mova.smoke.kondo-smoke
  (:require
   [clj-kondo.core :as kondo]
   [clojure.java.io :as io]
   [clojure.string :as str]
   [clojure.walk :as walk]))

;; ---------------------------------------------------------------------
;; Locate the repo root by walking up from cwd looking for mova/PLAN.md,
;; so the corpus resolves the same way regardless of the caller's cwd
;; (the JVM oracle is run from cli/, Mova from the repo root -- see
;; mova/bin/lsp-mova). NOT `*file*`-based: unsupported when Mova runs a
;; file directly.
;; ---------------------------------------------------------------------

(defn- find-repo-root [start]
  (loop [dir (.getCanonicalFile start)]
    (cond
      (.exists (io/file dir "mova" "PLAN.md")) dir
      (nil? (.getParentFile dir)) (throw (ex-info "kondo_smoke: could not find repo root (no mova/PLAN.md found walking up from cwd)" {:start (str start)}))
      :else (recur (.getParentFile dir)))))

(def repo-root (find-repo-root (io/file (System/getProperty "user.dir"))))

(defn- rel [^java.io.File f]
  (let [root-path (str (.getCanonicalPath repo-root) "/")
        p (.getCanonicalPath f)]
    (if (str/starts-with? p root-path)
      (subs p (count root-path))
      p)))

(def corpus
  ;; 7 real .clj files (small-to-medium, varied) + 1 real .cljc + 1
  ;; deliberately-buggy fixture (unused binding, arity mismatch,
  ;; unresolved bare and namespaced symbols, redundant do).
  (mapv #(io/file repo-root %)
        ["lib/src/clojure_lsp/producer.clj"
         "lib/src/clojure_lsp/http.clj"
         "lib/src/clojure_lsp/logger.clj"
         "lib/src/clojure_lsp/diff.clj"
         "lib/src/clojure_lsp/source_paths.clj"
         "lib/src/clojure_lsp/settings.clj"
         "lib/src/clojure_lsp/config.clj"
         "cli/integration-test/sample-test/src/sample_test/linked_editing_range/a.cljc"
         "mova/smoke/fixtures/buggy.clj"]))

(def config-dir (io/file repo-root "mova" "smoke" "fixtures" ".clj-kondo"))

;; Mirrors clojure-lsp.kondo/config-for-internal-paths's :config
;; (full-analysis? true: none of our corpus is :project-only).
(def kondo-config
  {:cache false
   ;; MOVA-GAP: clojure-lsp.kondo/config-for-internal-paths sets
   ;; :parallel true; clj-kondo's parallel-analyze path needs a real
   ;; java.util.concurrent.LinkedBlockingDeque, not implemented on Mova
   ;; yet (see mova/NOTES.md). :parallel only changes EXECUTION
   ;; strategy, not the analysis/findings content -- this script sorts
   ;; every bucket before printing, so the oracle diff is unaffected by
   ;; which strategy produced it.
   :parallel false
   :config-dir (str config-dir)
   :copy-configs false
   :lint [(str/join (System/getProperty "path.separator") (map str corpus))]
   :config {:output {:canonical-paths true}
            :analysis {:arglists true
                       :locals true
                       :keywords true
                       :protocol-impls true
                       :java-class-definitions false
                       :java-member-definitions false
                       :instance-invocations true
                       :java-class-usages true
                       :context [:clojure.test :re-frame.core]
                       :var-definitions {:meta [:arglists :style/indent]
                                         :callstack true}
                       :symbols true}}})

;; ---------------------------------------------------------------------
;; Normalization: sorted, relative-path, deterministic.
;; ---------------------------------------------------------------------

(defn- normalize-filename [x]
  (cond
    (string? x) (rel (io/file x))
    (nil? x) x
    :else x))

(defn- by-pr [a b] (compare (pr-str a) (pr-str b)))

(defn- canon
  "Sets and maps print in hash order, which Clojure leaves unspecified: sort them."
  [v]
  (walk/postwalk
   (fn [x]
     (cond (set? x) (into (sorted-set-by by-pr) x)
           (and (map? x) (not (record? x)) (not (sorted? x))) (into (sorted-map-by by-pr) x)
           :else x))
   v))

(defn- normalize-element [el]
  (into (sorted-map)
        (for [[k v] el]
          [k (cond
               (= k :filename) (normalize-filename v)
               (= k :uri) (str/replace (str v) (str (.getCanonicalPath repo-root) "/") "<root>/")
               :else (canon v))])))

(defn- sort-key [el]
  ;; A total order over heterogeneous finding/analysis maps: row/col
  ;; first (position in file), then a printable fallback so ties (e.g.
  ;; two locals at the same position) are still deterministic.
  [(:filename el) (:row el) (:col el) (:name-row el) (:name-col el)
   (pr-str (dissoc el :filename))])

(defn- normalize-bucket [elements]
  (->> elements
       (map normalize-element)
       (sort-by sort-key)
       vec))

(defn- normalize-findings [findings]
  ;; Raw clj-kondo.core/run! :findings is a flat vector of finding maps,
  ;; each carrying its own :filename -- NOT grouped by file (that
  ;; grouping is clojure-lsp.kondo's own `normalize`/`group-by :uri`
  ;; layer, one level up from the plain clj-kondo.core API this smoke
  ;; script calls directly).
  (into (sorted-map)
        (for [[filename fs] (group-by #(normalize-filename (:filename %)) findings)]
          [filename (normalize-bucket fs)])))

(defn- normalize-analysis [analysis]
  (into (sorted-map)
        (for [[bucket elements] (sort-by key analysis)]
          [bucket (normalize-bucket elements)])))

;; ---------------------------------------------------------------------
;; Run.
;; ---------------------------------------------------------------------

(defn -main [& _args]
  (let [{:keys [findings analysis summary]} (kondo/run! kondo-config)]
    (println "=== findings ===")
    (doseq [[filename file-findings] (normalize-findings findings)]
      (println filename)
      (doseq [f file-findings]
        (prn f)))
    (println "=== analysis ===")
    (doseq [[bucket elements] (normalize-analysis analysis)]
      (println bucket (count elements))
      (doseq [el elements]
        (prn el)))
    (println "=== summary ===")
    (prn (select-keys summary [:error :warning]))))

(-main)
