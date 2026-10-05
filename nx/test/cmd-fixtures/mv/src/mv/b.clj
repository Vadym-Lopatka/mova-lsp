(ns mv.b
  (:require [mv.a :as a :refer [plain]]))

(defn bee [n] (* n 2))

(defn uses-a [] (a/helper "x") (plain 3) (a/plain2 4))
