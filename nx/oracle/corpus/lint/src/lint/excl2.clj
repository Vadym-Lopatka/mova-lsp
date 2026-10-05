(ns lint.excl2
  (:refer-clojure :exclude [nonexistent-fn filter map])
  (:require [clojure.string :refer [join]]))

(defn filter [x] x)
(defn g [] (map 1 2))
