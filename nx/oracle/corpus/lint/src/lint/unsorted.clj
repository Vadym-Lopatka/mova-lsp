(ns lint.unsorted
  (:require [lint.helper :as h]
            [clojure.string :as str]
            [clojure.set :as set]
            [clojure.walk :as walk])
  (:import (java.util UUID Date)
           (java.io File)))

(defn f [] (str/join "," [(h/pub 1) (set/union #{} #{}) (walk/walk identity identity []) (UUID/randomUUID) (Date.) (File. "x")]))
