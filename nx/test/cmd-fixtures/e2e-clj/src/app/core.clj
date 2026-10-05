(ns app.core
  (:require [app.util :as u]
            [clojure.string :as str]))

(defn add [a b] (+ a b))

(defn run [xs]
  (let [unused 1
        m (map inc xs)]
    (u/twice (println (add 1 (reduce + m))))
    (str/upper-case (u/greet (u/->Person "bob")))
    [(u/now) (u/fresh-id) Integer/MAX_VALUE]))
