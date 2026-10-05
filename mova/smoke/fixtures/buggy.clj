(ns mova.smoke.fixtures.buggy
  (:require [clojure.string :as str]))

(defn add2
  "Adds two numbers."
  [a b]
  (+ a b))

(defn unused-binding-fn []
  (let [unused-x 1
        used-y 2]
    used-y))

(defn arity-mismatch-fn []
  (add2 1))

(defn unresolved-symbol-fn []
  (this-symbol-does-not-exist 1 2))

(defn redundant-do-fn []
  (do
    (+ 1 1)))

(defn unresolved-namespaced-fn []
  (str/this-fn-does-not-exist "x"))
