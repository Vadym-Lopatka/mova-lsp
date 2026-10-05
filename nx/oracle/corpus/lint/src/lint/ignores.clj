(ns lint.ignores
  (:require [lint.helper :as h]))

(defn a [x]
  #_:clj-kondo/ignore
  (let [unused 1] x))

(defn b [x]
  #_{:clj-kondo/ignore [:unused-binding]}
  (let [unused 1] x))

(defn c [x]
  #_{:clj-kondo/ignore [:unused-namespace]}
  (let [unused 1] x))

(defn d [x]
  #_:clj-kondo/ignore
  (let [fine 1] (+ fine x)))

(defn e [x]
  #_{:clj-kondo/ignore [:unresolved-symbol]}
  (nothing-here x))

#_:clj-kondo/ignore
(defn f [unused] 1)

(defn g [x]
  (let [#_:clj-kondo/ignore unused 1] x))
