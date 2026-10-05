;; mova/smoke/kondo_equal.clj -- clj-kondo output fingerprint on the
;; kondo_bench :dir corpus: one line for findings, one per :analysis bucket.
;; Usage: mova/bin/lsp-mova mova/smoke/kondo_equal.clj   (diff across builds)
(ns mova.smoke.kondo-equal
  (:require [clj-kondo.core :as kondo]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [clojure.walk :as walk]))

(def root (loop [d (.getCanonicalFile (io/file (System/getProperty "user.dir")))]
            (if (.exists (io/file d "mova" "PLAN.md")) d (recur (.getParentFile d)))))

(def files (->> (.listFiles (io/file root "lib/src/clojure_lsp/feature"))
                (filter #(str/ends-with? (.getName ^java.io.File %) ".clj"))
                (sort-by #(.getName ^java.io.File %))
                (take 20)))

(def res (kondo/run! {:cache false :parallel false :copy-configs false
                      :config-dir (str (io/file root "mova/smoke/fixtures/.clj-kondo"))
                      :lint [(str/join (System/getProperty "path.separator") (map str files))]
                      :config {:output {:canonical-paths true}
                               :analysis {:arglists true :locals true :keywords true :protocol-impls true
                                          :java-class-definitions false :java-member-definitions false
                                          :instance-invocations true :java-class-usages true
                                          :context [:clojure.test :re-frame.core]
                                          :var-definitions {:meta [:arglists :style/indent] :callstack true}
                                          :symbols true}}}))

(defn- canon [x] ;; sort map keys so printing is order-independent
  (walk/postwalk #(if (map? %) (into (sorted-map-by (fn [a b] (compare (pr-str a) (pr-str b)))) %) %) x))

(defn- line [label xs]
  (let [ss (sort (map (comp pr-str canon) xs))]
    (println label "count" (count ss) "hash" (hash (str/join "\n" ss)))))

(line :findings (:findings res))
(doseq [[k v] (sort-by key (:analysis res))] (line k v))
