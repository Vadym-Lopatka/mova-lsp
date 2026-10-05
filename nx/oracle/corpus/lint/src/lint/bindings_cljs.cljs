(ns lint.bindings-cljs)

(defn f [a b] a)
(defn g []
  (let [x 1 y 2] x))
