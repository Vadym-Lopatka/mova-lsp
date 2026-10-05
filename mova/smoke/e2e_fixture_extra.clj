(ns sample-test.e2e-smoke
  (:require [clojure.string :as str]))

(defn e2e-helper
  "Uppercases the given string. Used by the Emacs e2e smoke test."
  [x]
  (str/upper-case x))

(defn e2e-caller []
  (e2e-helper "hi"))

(defn e2e-rename-me [] :original)

(defn e2e-broken []
  (e2e-totally-unresolved-fn 1 2))

(defn   e2e-format-me [a b]
  (+ a
     b))
