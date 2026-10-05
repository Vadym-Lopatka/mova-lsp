(ns lint.discouraged-cljs)

(defn f [x]
  (gensym "a")
  (spit "f" "x")
  (println x)
  (rand-int 4))
