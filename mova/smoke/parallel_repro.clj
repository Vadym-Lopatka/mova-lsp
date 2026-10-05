;; parallel clj-kondo repro: lint ~40 lib/src files N times with :parallel true,
;; :cache false, print failure count per run. Repro for the intermittent
;; "Can't parse <file>, Unable to resolve symbol: .sym" bug under :parallel true.
(require '[clj-kondo.core :as k])
(require '[clojure.java.io :as io])

(def files
  (->> (file-seq (io/file "lib/src"))
       (filter #(.isFile %))
       (filter #(re-find #"\.clj[cs]?$" (.getName %)))
       (map #(.getPath %))
       (take 40)))

(println "files:" (count files))

(def n (if-let [a (first *command-line-args*)] (Long/parseLong a) 20))

(def fail-count (atom 0))

(dotimes [i n]
  (let [result (k/run! {:lint files :parallel true :cache false})
        findings (:findings result)
        parse-errors (filter #(and (= :syntax (:type %))
                                   (re-find #"Unable to resolve symbol: \.sym" (str (:message %))))
                             findings)]
    (if (seq parse-errors)
      (do (swap! fail-count inc)
          (println "run" i "FAIL" (count parse-errors) "parse errors"))
      (println "run" i "ok"))))

(println "TOTAL FAILURES:" @fail-count "/" n)
