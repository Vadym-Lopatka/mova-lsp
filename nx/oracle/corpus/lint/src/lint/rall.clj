(ns lint.rall
  (:require [clojure.set :refer :all :as set]
            [clojure.walk :refer :all :as walk]))

(defn f []
  (union #{1} #{2})
  (set/union #{1} #{2})
  (set/difference #{1} #{2})
  (walk/postwalk inc [1])
  (postwalk inc [1])
  (map set/rename-keys [{}] [{}]))
