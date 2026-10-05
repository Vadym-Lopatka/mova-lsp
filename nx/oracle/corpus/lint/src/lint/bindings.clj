(ns lint.bindings)

(defn unused-arg [a b] a)
(defn unused-let [x]
  (let [y 1 z 2]
    (+ x y)))
(defn unused-destructure [{:keys [a b] :as m} [c d & more]]
  (+ a c))
(defn underscore-ok [_x _]
  1)
(defn unused-loop []
  (loop [i 0 j 1]
    (when (< i 10) (recur (inc i) j))))
(defn unused-fn-arg []
  (fn [x y] x))
(defn unused-named-fn []
  (fn foo [x] 1))
(defn unused-binding-in-for []
  (for [x [1 2] y [3 4]] x))
(defn unused-doseq []
  (doseq [x [1] y [2]] (println x)))
(defn unused-if-let [m]
  (if-let [x (:a m)] 1 2))
(defn unused-when-let [m]
  (when-let [x (:a m)] 1))
(defn unused-multi
  ([a] 1)
  ([a b] b))
(defn unused-letfn []
  (letfn [(f [x] 1) (g [y] y)]
    (g 1)))
(defn unused-as [{:as m}] 1)
(defn unused-or [{:keys [a] :or {a 1}}] 1)
(defn unused-catch []
  (try 1 (catch Exception e 2)))
(defn unused-macro-let []
  (let [a 1]
    (-> 1 inc)))
(defn used-in-quote [x]
  (let [y 1] `(~y ~x)))
(defn unused-binding-shadow [x]
  (let [x 1] x))
(defn unused-anon [] (map #(inc 1) [1 2]))
(defmacro unused-macro-arg [a b] `(+ ~a 1))
(defn destructure-strs [{:strs [a b]} {:syms [c d]}] a)
(defn destructure-ns-keys [{:keys [x/a y/b]}] a)
(defn unused-rest [& args] 1)
(defn nested [[a [b c]]] a)
(defn binding-form []
  (binding [*out* *out*] 1))
(defn with-open-unused []
  (with-open [r (java.io.StringReader. "x")] 1))
(defn unused-dotimes []
  (dotimes [i 3] 1))
(defn unused-when-some [m]
  (when-some [x m] 1))
(defn unused-if-some [m]
  (if-some [x m] 1 2))
(defn unused-when-first [m]
  (when-first [x m] 1))
(defn unused-as-> [m]
  (as-> m x 1))
(defn unused-let-underscore []
  (let [_ 1 _y 2] 3))
(defn unused-reify []
  (reify Object (toString [this] "x")))
(defn unused-defrecord-like []
  (proxy [Object] [] (toString [] "x")))
(defrecord Rec [a b])
(deftype Ty [a b]
  Object
  (toString [this] "x"))
