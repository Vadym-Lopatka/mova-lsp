(ns lint.unresolved-cljc)

(defn f []
  #?(:clj (undefined-clj 1) :cljs (undefined-cljs 1))
  (undefined-both 1))
