(ns lint.ns-misc
  (:require [clojure.string :as str :refer [join]]
            [clojure.string :as str2])
  (:require [clojure.set :as set]))

(defn f [] (join "," []) (set/union #{} #{}))
