(ns lint.bindings-cljc)

(defn f [a b] a)
(defn g []
  (let [#?(:clj x :cljs y) 1 z 2] z))
(defn h [x]
  #?(:clj (let [a 1] x)
     :cljs (let [b 1] x)))
