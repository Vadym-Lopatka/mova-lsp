(ns nsx.a5
  (:import (java.util Date)
           (java.io File))
  (:require [clojure.data :as data]
            [clojure.zip :as zip]))

(defn f [] (Date.) (data/diff 1 2))
