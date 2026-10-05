(ns lint.discouraged
  (:require [clojure.string :as str]
            [lint.helper :as h]
            [lint.helper :refer [pub]]))

(defn f [x]
  (gensym "a")
  (gensym)
  (println x)
  (slurp "f")
  (spit "f" "x")
  (str/trim " a ")
  (str/trim " a " 1)
  (rand)
  (rand-int 3)
  (map gensym [1])
  (map rand-int [1])
  (apply rand [])
  (h/pub 1)
  (pub 1)
  (clojure.core/gensym)
  (let [gensym inc] (gensym 1))
  #_(gensym "ignored")
  (comment (gensym "in comment"))
  (var gensym)
  #'println
  `(gensym ~x))
