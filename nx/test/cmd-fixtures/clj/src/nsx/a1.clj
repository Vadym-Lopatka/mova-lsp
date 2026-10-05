(ns nsx.a1
  "doc"
  (:require [clojure.string :as str]
            [clojure.set :as set]
            [clojure.walk :refer [postwalk prewalk walk keywordize-keys stringify-keys postwalk-replace]]
            [clojure.java.io :as io]   ; trailing comment
            ;; leading comment
            [clojure.edn :as edn])
  (:import [java.util Date UUID]
           (java.io File InputStream OutputStream Reader Writer)
           java.net.URL))

(defn f [] (str/join (set/union #{1} #{2}) (postwalk identity 1)) (Date.) (File. "x") (io/file "x"))
