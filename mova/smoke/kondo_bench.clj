;; mova/smoke/kondo_bench.clj
;;
;; Measures clj-kondo.core/run! analysis speed in µs/source-byte, using
;; the same call shape as mova/smoke/kondo_smoke.clj / clojure-lsp.kondo/
;; config-for-internal-paths: :cache false, :parallel false, full
;; :analysis config.
;;
;; Two corpora:
;;   :file - one real mid-size file (lib/src/clojure_lsp/kondo.clj, ~28KB)
;;   :dir  - 20 real files (lib/src/clojure_lsp/feature/*.clj, first 20
;;           alphabetically, ~248KB)
;;
;; For each corpus: 1 cold run (first call in this process), then 3 more
;; in-process repeats; prints the median of those 3 as "warm".
;;
;; Usage:
;;   JVM:  cd cli && clojure -M ../mova/smoke/kondo_bench.clj
;;   Mova: mova/bin/lsp-mova mova/smoke/kondo_bench.clj
;;         MOVA_JIT=1 MOVA_BIN=<jit-build> mova/bin/lsp-mova mova/smoke/kondo_bench.clj
(ns mova.smoke.kondo-bench
  (:require
   [clj-kondo.core :as kondo]
   [clojure.java.io :as io]
   [clojure.string :as str]))

(defn- find-repo-root [start]
  (loop [dir (.getCanonicalFile start)]
    (cond
      (.exists (io/file dir "mova" "PLAN.md")) dir
      (nil? (.getParentFile dir)) (throw (ex-info "kondo_bench: could not find repo root (no mova/PLAN.md found walking up from cwd)" {:start (str start)}))
      :else (recur (.getParentFile dir)))))

(def repo-root (find-repo-root (io/file (System/getProperty "user.dir"))))
(defn- f [rel] (io/file repo-root rel))

(def config-dir (f "mova/smoke/fixtures/.clj-kondo"))

(def corpora
  {:file [(f "lib/src/clojure_lsp/kondo.clj")]
   :handlers [(f "lib/src/clojure_lsp/handlers.clj")]
   :dir  (->> (.listFiles (io/file repo-root "lib/src/clojure_lsp/feature"))
              (filter #(str/ends-with? (.getName ^java.io.File %) ".clj"))
              (sort-by #(.getName ^java.io.File %))
              (take 20)
              vec)})

(defn- total-bytes [files] (reduce + (map #(.length ^java.io.File %) files)))

(def par? (= "1" (System/getenv "KONDO_PARALLEL")))

(defn- run-once [files]
  (let [t0 (System/nanoTime)]
    (kondo/run! {:cache false
                 :parallel par?
                 :config-dir (str config-dir)
                 :copy-configs false
                 :lint (if par? (mapv str files) [(str/join (System/getProperty "path.separator") (map str files))])
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
    (/ (- (System/nanoTime) t0) 1e6)))

(defn- median [xs]
  (nth (sort xs) (quot (count xs) 2)))

(def only (some-> (System/getenv "KONDO_CORPUS") keyword))

(doseq [[label files] (if only (select-keys corpora [only]) (dissoc corpora :handlers))]
  (let [bytes (total-bytes files)
        cold-ms (run-once files)
        rest-ms (vec (repeatedly 3 #(run-once files)))
        warm-ms (median rest-ms)]
    (println
      (format "corpus=%s files=%d bytes=%d cold_ms=%.2f cold_us_per_byte=%.3f warm_ms=%.2f warm_us_per_byte=%.3f warm_samples_ms=%s"
              (name label) (count files) bytes
              cold-ms (/ (* cold-ms 1000.0) bytes)
              warm-ms (/ (* warm-ms 1000.0) bytes)
              (mapv #(format "%.2f" %) rest-ms)))))
