;; mova/smoke/sci_smoke.clj
;;
;; Exercises the sci.core shim (mova/shims/sci/{core,ctx_store}.mova,
;; mova/PLAN.md "hooks (sci)" wave) end to end, two ways:
;;
;; 1. A real clj-kondo `:hooks {:analyze-call {...}}` config whose hook
;;    code lives in a fixture `.clj-kondo/` dir
;;    (mova/smoke/fixtures/hooks/.clj-kondo/hooks/my_hook.clj) and emits a
;;    custom finding via `clj-kondo.hooks-api/reg-finding!`. This exercises
;;    `clj-kondo.impl.hooks` (mova/overlay/clj_kondo/impl/hooks.mova):
;;    `sci/create-ns`, `sci/init` (:namespaces/:classes/:imports/:load-fn),
;;    `sci/binding`, `sci/eval-string*`, `sci.ctx-store`.
;;
;; 2. `clojure-lsp.feature.diagnostics.custom`'s private `analyze` fn,
;;    called directly against a small fixture linter
;;    (mova/smoke/fixtures/custom_linter/clojure-lsp.exports/linters/my/linter.clj,
;;    loaded via `:load-fn`'s classpath scan, same shape
;;    `clojure-lsp.exports/linters/...` real projects use).
;;
;; Oracle: `cd cli && clojure -M ../mova/smoke/sci_smoke.clj`
;; Mova:   `MOVA_BIN=... mova/bin/lsp-mova mova/smoke/sci_smoke.clj`
;;         (run from the clojure-lsp-kondo repo root)
(ns mova.smoke.sci-smoke
  (:require
   [clj-kondo.core :as kondo]
   [clojure-lsp.feature.diagnostics.custom :as custom]
   [clojure.java.io :as io]
   [clojure.string :as str]
   [clojure.walk :as walk]))

(defn- find-repo-root [start]
  (loop [dir (.getCanonicalFile start)]
    (cond
      (.exists (io/file dir "mova" "PLAN.md")) dir
      (nil? (.getParentFile dir)) (throw (ex-info "sci_smoke: could not find repo root" {:start (str start)}))
      :else (recur (.getParentFile dir)))))

(def repo-root (find-repo-root (io/file (System/getProperty "user.dir"))))

(defn- rel [f]
  (let [root-path (str (.getCanonicalPath repo-root) "/")
        p (.getCanonicalPath (io/file f))]
    (if (str/starts-with? p root-path)
      (subs p (count root-path))
      p)))

(defn- by-pr [a b] (compare (pr-str a) (pr-str b)))

(defn- canon [v]
  (walk/postwalk
   (fn [x]
     (cond (set? x) (into (sorted-set-by by-pr) x)
           (and (map? x) (not (record? x)) (not (sorted? x))) (into (sorted-map-by by-pr) x)
           :else x))
   v))

;; ---------------------------------------------------------------------
;; 1. clj-kondo :hooks
;; ---------------------------------------------------------------------

(def hooks-fixture-dir (io/file repo-root "mova" "smoke" "fixtures" "hooks"))

(defn- run-hooks-test []
  (let [{:keys [findings]}
        (kondo/run! {:cache false
                     :parallel false
                     :config-dir (str (io/file hooks-fixture-dir ".clj-kondo"))
                     :lint [(str hooks-fixture-dir)]
                     :config {:output {:canonical-paths true}}})
        hook-findings (->> findings
                           (filter #(= :my-custom-hook (:type %)))
                           (map #(-> %
                                     (update :filename rel)
                                     (select-keys [:filename :row :col :type :message :level])))
                           canon
                           (sort-by (juxt :filename :row :col))
                           vec)]
    (println "=== hooks findings ===")
    (doseq [f hook-findings] (prn f))))

;; ---------------------------------------------------------------------
;; 2. clojure-lsp.feature.diagnostics.custom
;; ---------------------------------------------------------------------

(def custom-linter-classpath-dir (io/file repo-root "mova" "smoke" "fixtures" "custom_linter"))

(defn- run-custom-linter-test []
  (let [uri "file:///tmp/sci-smoke-fake.clj"
        result (#'custom/analyze 'my.linter/lint {} #{uri} {:classpath [(str custom-linter-classpath-dir)]})]
    (println "=== custom linter diagnostics ===")
    (doseq [[u diags] (into (sorted-map) result)]
      (println u)
      (doseq [d (canon diags)] (prn (dissoc d :uri))))))

(defn -main [& _args]
  (run-hooks-test)
  (run-custom-linter-test))

(-main)
