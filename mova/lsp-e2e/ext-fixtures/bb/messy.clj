(ns app.messy
(:require [clojure.set :as set]
   [clojure.walk   :as walk]))
(defn   messy-fn [a   b]
    (let [x   (+ a b)
  y   (set/union   #{x}   #{b})]
        (if (> x 1)
   (println   y)
      (do   (walk/postwalk   identity   y)
    nil))))
(defn messy-map[m]{:msg/a   1
 :msg/b (map   inc   [1 2 3])})
(defmulti messy-dispatch   :msg/a)
(defmethod messy-dispatch 1 [m]
   (:msg/b   m))
