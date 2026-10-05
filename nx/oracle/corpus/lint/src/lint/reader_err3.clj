(ns lint.reader-err3)

(defn f [] #"(")
(defn g [] {:a 1 :a 2})
(defn h [] 1)
