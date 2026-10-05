(ns mv.f
  (:require [mv.a :as a]))

(defn destr [{:keys [a b]} c] (+ a b c))

(defn with-map [m] (let [{:keys [x y]} m] (+ x y)))

(defmacro my-mac [& body] `(do ~@body))

(my-mac (println 1))

(defn pa [a b c] (+ a b c))

(defn use-pa [] (pa 1 2 3) (pa 4 5 6))

(defn nested [x] (let [{:keys [p q]} x] (a/plain (+ p q))))
