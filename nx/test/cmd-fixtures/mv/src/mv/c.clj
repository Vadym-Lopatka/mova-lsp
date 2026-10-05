(ns mv.c
  (:require [mv.a :refer [plain plain2]]
            [mv.b :as bb]))

(defn go [] (plain 1) (plain2 2) (bb/bee 1))
