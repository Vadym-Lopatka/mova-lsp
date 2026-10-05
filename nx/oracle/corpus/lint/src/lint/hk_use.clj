(ns lint.hk-use
  (:require [lint.hk :refer [one-of]]))

(defn f [x y]
  (when (one-of x [Object string number])
    (one-of y [foo bar]))
  (one-of x [])
  (one-of (inc x) [a b c]))
