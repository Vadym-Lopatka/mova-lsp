(ns lint.hof
  (:require [lint.helper :as h]
            [clojure.string :as str]))

(defn f [xs]
  (map h/nonexistent xs)
  (map str/nope xs)
  (filter h/nonexistent xs)
  (reduce h/nothing 0 xs)
  (map h/old xs)
  (swap! (atom 1) h/missing-fn)
  (update {} :a h/missing2)
  (map (fn [a b] a) xs)
  (map #(inc 1) xs)
  (map (let [g (fn [a] a)] g) xs))
(defn g [xs]
  (let [f (fn [a b] a)]
    (map f xs)
    (filter f xs)
    (reduce f xs)))
(defn letfn-arity []
  (letfn [(f [a] a) (g [a b] b)]
    (f) (g 1) (f 1) (g 1 2)))
