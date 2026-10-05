(ns lint.protocols
  (:require [lint.helper :as h]))

(defprotocol Shape
  (area [this])
  (perimeter [this])
  (scale [this k]))

(defrecord Sq [s]
  Shape
  (area [this] s))
(defrecord Sq2 [s]
  Shape
  (area [this] s)
  (perimeter [this] s)
  (scale [this k] s))
(defrecord Sq3 [s]
  Shape
  (area [this] s)
  (extra [this] s)
  (scale [this] s))
(deftype Ty [a]
  Shape
  (area [this] a))
(def r (reify Shape (area [this] 1)))
(def r2 (reify Shape (area [this] 1) (perimeter [this] 2) (scale [this k] 3)))
(def r3 (reify h/P (pm [this] 1)))
(extend-protocol Shape
  String
  (area [s] 1)
  Long
  (nothing [s] 2))
(extend-type String
  Shape
  (area [s] 1))
(extend Long Shape {:area (fn [x] 1)})
(defrecord Obj []
  Object
  (toString [this] "x"))
(defrecord Both [a]
  Shape
  (area [this] 1)
  h/P
  (pm [this] 2))
(deftype Iface []
  java.lang.Runnable
  (run [this] nil)
  clojure.lang.IFn
  (invoke [this] 1))
(defn use-shape [x] (area x) (area x 1) (scale x))
