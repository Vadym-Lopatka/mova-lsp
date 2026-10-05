(ns lint.rall-cljs
  (:require [clojure.string :refer :all :as str]))

(defn f []
  (str/join "," [1])
  (join "," [1]))
