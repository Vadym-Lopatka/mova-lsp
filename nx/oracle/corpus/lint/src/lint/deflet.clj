(ns lint.deflet
  (:require [borkdude.deflet :as deflet :refer [deflet defp]]))

(defn f []
  (deflet
    (def a 1)
    (def b (inc a))
    (println a b)
    (def c 3)
    (+ a b c)))

(defn g []
  (deflet/deflet
    (def x 1)
    x))

(defn h []
  (deflet
    (println 1)
    (def y 2)
    (inc y)
    (def z)))

(defn i []
  (deflet))

(defn j []
  (deflet 1 2 3))

(defn k []
  (deflet/defletp
    (def x 1)
    (def unused-binding 2)
    x))

(defp top-level-p 1)
(defn l [] (deflet (def only-def 1)))
