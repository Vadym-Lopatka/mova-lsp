(ns lint.discouraged-cljc)

(defn f [x]
  (gensym "a")
  #?(:clj (spit "f" "x") :cljs (spit "g" "y"))
  #?(:cljs (println x)))
