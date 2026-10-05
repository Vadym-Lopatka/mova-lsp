(ns lint.skipc
  {:clj-kondo/config '{:skip-comments true}}
  (:require [clojure.string :as str]
            [clojure.set :as set]))

(defn f [x] (inc x))

(comment
  (f 1 2)
  (undefined-fn 1)
  (str/join "," [1])
  [(g 1) {:a (h 2)}]
  unknown-sym
  (let [a 1] (b))
  (comment (set/union #{})))

(f 1 2)
