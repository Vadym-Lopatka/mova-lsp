;; mova/smoke/jar_smoke.clj
;;
;; External-classpath jar analysis: runs clj-kondo.core/run! with
;; :lint pointed straight at real jars from ~/.m2 (same entry point
;; kondo_smoke.clj uses for plain files) to exercise clj-kondo's
;; JarFile-reading path (clj_kondo/impl/core.clj's `sources-from-jar`,
;; via `(JarFile. jar-file)` + `enumeration-seq` + `.getInputStream`) --
;; the veneer added in src/hostclass.rs (mova/PLAN.md campaign: "external
;; classpath (jar) analysis"). Same normalization/print shape as
;; kondo_smoke.clj so the two smoke outputs are diffed the same way.
;;
;; Oracle: `cd cli && clojure -M ../mova/smoke/jar_smoke.clj`
;; Mova:   `MOVA_BIN=... mova/bin/lsp-mova mova/smoke/jar_smoke.clj`
;;         (run from the clojure-lsp-kondo repo root)
(ns mova.smoke.jar-smoke
  (:require
   [clj-kondo.core :as kondo]
   [clojure.java.io :as io]
   [clojure.string :as str]
   [clojure.walk :as walk]))

(defn- find-repo-root [start]
  (loop [dir (.getCanonicalFile start)]
    (cond
      (.exists (io/file dir "mova" "PLAN.md")) dir
      (nil? (.getParentFile dir)) (throw (ex-info "jar_smoke: could not find repo root (no mova/PLAN.md found walking up from cwd)" {:start (str start)}))
      :else (recur (.getParentFile dir)))))

(def repo-root (find-repo-root (io/file (System/getProperty "user.dir"))))

(def m2-root (io/file (System/getProperty "user.home") ".m2" "repository"))

;; 3 small, real jars from ~/.m2 (already on cli's own classpath --
;; `cd cli && clojure -Spath`), each with a handful of real .clj source
;; files inside: enough to exercise the jar-reading path without a slow
;; full-classpath scan.
(def jars
  [(io/file m2-root "medley" "medley" "1.4.0" "medley-1.4.0.jar")
   (io/file m2-root "borkdude" "rewrite-edn" "0.5.9" "rewrite-edn-0.5.9.jar")
   (io/file m2-root "camel-snake-kebab" "camel-snake-kebab" "0.4.3" "camel-snake-kebab-0.4.3.jar")])

(doseq [j jars]
  (when-not (.exists j)
    (throw (ex-info "jar_smoke: missing jar (run `cd cli && clojure -Spath` to check ~/.m2 paths)" {:jar (str j)}))))

(def kondo-config
  {:cache false
   :parallel false
   :copy-configs false
   :lint [(str/join (System/getProperty "path.separator") (map str jars))]
   :config {:output {:canonical-paths true}
            :analysis {:arglists true
                       :locals false
                       :keywords true
                       :protocol-impls true
                       :java-class-definitions false
                       :java-member-definitions false
                       :instance-invocations true
                       :java-class-usages true
                       :var-definitions {:meta [:arglists :style/indent]
                                         :callstack true}
                       :symbols true}}})

;; ---------------------------------------------------------------------
;; Normalization: same shape as kondo_smoke.clj (sorted, relative
;; jar-entry paths, deterministic set/map printing). A jar entry's
;; :filename is `<jar-path>:<entry-path>` (real clj-kondo jar-entry
;; naming, :output {:canonical-paths true}) -- normalized to `<m2>/...`
;; so the golden is stable across machines/usernames.
;; ---------------------------------------------------------------------

(defn- normalize-filename [x]
  (cond
    (string? x) (str/replace x (.getCanonicalPath m2-root) "<m2>")
    (nil? x) x
    :else x))

(defn- by-pr [a b] (compare (pr-str a) (pr-str b)))

(defn- canon [v]
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
               :else (canon v))])))

(defn- sort-key [el]
  [(:filename el) (:row el) (:col el) (:name-row el) (:name-col el)
   (pr-str (dissoc el :filename))])

(defn- normalize-bucket [elements]
  (->> elements
       (map normalize-element)
       (sort-by sort-key)
       vec))

(defn- normalize-findings [findings]
  (into (sorted-map)
        (for [[filename fs] (group-by #(normalize-filename (:filename %)) findings)]
          [filename (normalize-bucket fs)])))

(defn- normalize-analysis [analysis]
  (into (sorted-map)
        (for [[bucket elements] (sort-by key analysis)]
          [bucket (normalize-bucket elements)])))

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
