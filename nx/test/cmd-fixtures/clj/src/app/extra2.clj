(ns app.extra2
  (:require [clojure.string :as str :refer [join split trim]]
            [clojure.set :as set]
            [clojure.walk :as walk]
            [clojure.data :refer :all]
            [clojure.edn :as edn]
            [clojure.set :as set2]
            ;; a comment about zip
            [clojure.zip :as zip]
            [clojure.pprint :refer [pprint print-table]])
  (:import (java.util Date UUID Random)
           java.io.File
           [java.net URL URI]))

(defn f []
  (join "," (split "a b" #" "))
  (str/trim " x")
  (set/union #{1} #{2})
  ::kw :app.extra2/other
  (Date.) (File. "x") (URL. "http://x"))
