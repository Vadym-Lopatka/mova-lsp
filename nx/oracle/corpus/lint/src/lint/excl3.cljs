(ns lint.excl3
  (:refer-clojure :exclude [nonexistent-fn filter map clj->js print-str]))

(defn g [] (print-str 1))
