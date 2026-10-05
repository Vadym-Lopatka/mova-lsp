(ns nx.oracle
  "JVM oracle: runs clj-kondo with clojure-lsp's own config fns, writes normalized per-file goldens."
  (:require
   [cheshire.core :as json]
   [clojure-lsp.kondo :as lsp.kondo]
   [clojure.java.io :as io]
   [clojure.string :as string]
   [clj-kondo.core :as kondo])
  (:import [java.io File]))

(set! *warn-on-reflection* true)

(def ^:private internal-cfg @#'lsp.kondo/config-for-internal-paths)
(def ^:private copy-cfg @#'lsp.kondo/config-for-copy-configs)
(def ^:private external-cfg @#'lsp.kondo/config-for-external-paths)

(defn- canon [^String p] (.getCanonicalPath (io/file p)))

(defn- plain
  "Keywords/symbols -> strings, sets/seqs -> vectors (sets sorted), drop nothing."
  [x]
  (cond
    (map? x) (into {} (map (fn [[k v]] [(if (keyword? k) (subs (str k) 1) (str k)) (plain v)])) x)
    (set? x) (vec (sort-by pr-str (map plain x)))
    (sequential? x) (mapv plain x)
    (keyword? x) (subs (str x) 1)
    (symbol? x) (str x)
    (or (string? x) (number? x) (boolean? x) (nil? x)) x
    :else (str x)))

(defn- sort-key [m]
  [(or (get m "row") 0) (or (get m "col") 0) (or (get m "end-row") 0) (or (get m "end-col") 0)
   (str (or (get m "name") (get m "to") (get m "type") "")) (json/generate-string m)])

(defn- rel-name
  "Relative file name: under root -> relative; jar entries -> <jar-basename>!/<entry>."
  [^String root ^String fname]
  (let [[path entry] (string/split fname #":(?!\\)" 2)
        jar? (and entry (.endsWith ^String path ".jar"))]
    (cond
      jar? (str (.getName (io/file path)) "!/" entry)
      (and root (.startsWith fname (str root File/separator))) (subs fname (inc (count root)))
      :else fname)))

(defn- lang-of [^String f]
  (let [e (last (string/split f #"\."))]
    (if (#{"clj" "cljs" "cljc"} e) e "other")))

(defn- group [root res]
  (let [files (volatile! {})
        add (fn [f path v] (vswap! files update-in (into [(rel-name root f)] path) (fnil conj []) v))]
    (doseq [[bucket els] (:analysis res)
            el els
            :let [f (:filename el)]
            :when f]
      (add f [:analysis bucket] (plain (dissoc el :filename))))
    (doseq [fd (:findings res)
            :let [f (:filename fd)]
            :when f]
      (add f [:findings] (plain (dissoc fd :filename))))
    @files))

(defn- renumber-ids
  "kondo local ids come from a global counter (non-deterministic with :parallel). Renumber per file
  by rank in sorted :locals order, in both locals and local-usages."
  [analysis]
  (let [locals (sort-by sort-key (get analysis "locals"))
        idm (into {} (map-indexed (fn [i l] [(get l "id") (inc i)])) locals)
        re (fn [els] (mapv #(cond-> % (contains? % "id") (update "id" (fn [i] (get idm i i)))) els))]
    (cond-> analysis
      (contains? analysis "locals") (update "locals" re)
      (contains? analysis "local-usages") (update "local-usages" re))))

(defn- write-file! [^File out-dir rel data]
  (let [f (io/file out-dir (str rel ".json"))
        analysis (->> (:analysis data)
                      (into (sorted-map) (map (fn [[b els]] [(if (keyword? b) (name b) b) els])))
                      renumber-ids
                      (into (sorted-map) (map (fn [[b els]] [b (vec (sort-by sort-key els))]))))
        doc {"file" rel "lang" (lang-of rel) "analysis" analysis
             "findings" (vec (sort-by sort-key (:findings data)))}]
    (io/make-parents f)
    (spit f (json/generate-string doc {:pretty false}))))

(defn- lsp-db [root-dir]
  {:project-root-uri (str (.toURI (io/file root-dir)))
   :env :unit-test
   :project-analysis-type :project-and-full-dependencies})

(defn- run [cfg]
  (let [err (java.io.StringWriter.)]
    (binding [*err* err] (kondo/run! cfg))))

(defn- source-files [paths]
  (for [p paths
        ^File f (file-seq (io/file p))
        :when (and (.isFile f) (re-find #"\.clj[sc]?$" (.getName f)))]
    (.getCanonicalPath f)))

(defn- cfg-with-cache
  "Test-only tweak: no copy-configs writes."
  [cfg]
  (assoc cfg :copy-configs false))

(defn -main
  "Args: project|jars <root> <out-dir> <paths-file> [<jars-file>]. paths-file: one path per line."
  [mode root out paths-file & [jars-file]]
  (let [root (canon root)
        out-dir (io/file out)
        lines (fn [f] (when f (->> (string/split-lines (slurp f)) (remove string/blank?) (mapv canon))))
        paths (lines paths-file)
        jars (lines jars-file)
        db (lsp-db root)
        t0 (System/nanoTime)
        ms #(long (/ (- (System/nanoTime) %) 1e6))
        ext (fn [ps] (run (cfg-with-cache (external-cfg ps db nil))))]
    (case mode
      "project"
      (let [_ (when (seq jars)
                (run (copy-cfg jars db)) ; same order as clojure-lsp startup: copy configs, then jar analysis
                (ext jars))
            t-jar (ms t0)
            t1 (System/nanoTime)
            res (run (cfg-with-cache (internal-cfg paths db nil)))
            t-run (ms t1)
            files (group root res)
            srcs (map #(rel-name root %) (source-files paths))]
        (doseq [rel (distinct (concat srcs (keys files)))]
          (write-file! out-dir rel (get files rel)))
        (println (json/generate-string {:corpus-files (count (distinct (concat srcs (keys files))))
                                        :jar-prepass-ms (when (seq jars) t-jar)
                                        :kondo-ms t-run :total-ms (ms t0)})))
      "jars"
      (let [t1 (System/nanoTime)
            res (ext paths)
            t-run (ms t1)
            files (group nil res)]
        (doseq [[rel data] files]
          (write-file! out-dir rel data))
        (println (json/generate-string {:corpus-files (count files) :kondo-ms t-run :total-ms (ms t0)}))))
    (shutdown-agents)))
