(ns lint.arity-cljs)

(defn f [a] a)
(defn g []
  (f)
  (f 1 2)
  (inc)
  (js/foo 1 2 3)
  (js/parseInt)
  (subs "x"))
