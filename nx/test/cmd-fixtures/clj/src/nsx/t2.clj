(ns nsx.t2)

(defn add [a b] (+ a b))

(defn use-add []
  (add 1 2)
  (add 3 (inc 4)))

(defn- helper [x] (* x x))

(defn run [xs]
  (map #(+ % 1) xs)
  (map (fn [a] (helper a)) xs)
  (map #(helper %) xs)
  (for [x xs
        y xs]
    (* (helper x) y))
  (doseq [y xs]
    (println (* y 2))))

(defn undefined-caller []
  (unknown-private-fn 1 2)
  (->> [1 2] (map inc) (filter unknown-fn2))
  (mystery/thing 3))
