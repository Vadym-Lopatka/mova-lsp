(ns mv.a
  (:require [clojure.string :as str]
            [mv.b :as b]))

(def x 1)

(defn helper [s] (str/upper-case s))

(defn user-of-helper [s] (helper s))

(defn uses-b [] (b/bee 2))

(defn calls-x [] (+ x 1))

(defn plain [a] (inc a))

(defn plain2 [a] (dec a))
