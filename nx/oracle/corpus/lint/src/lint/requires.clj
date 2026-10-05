(ns lint.requires
  (:require
   [clojure.string :as str]
   [clojure.set :as set]
   [clojure.walk :as walk]
   [clojure.java.io :as io]
   [clojure.edn :refer [read-string]]
   [clojure.data :refer [diff]]
   [clojure.pprint :refer [pprint cl-format] :as pp]
   [clojure.zip]
   [clojure.test :refer :all]
   [lint.helper :as h :refer [pub two]]
   [lint.helper :as h2]
   [lint.nonexistent :as nx]
   [lint.nonexistent2 :refer [foo]]
   [lint.helper :refer [multi]]
   lint.helper)
  (:import
   (java.util Date List)
   (java.io File)
   java.util.UUID
   [java.util ArrayList HashMap]))

(defn use-some []
  (str/join "," [1 2])
  (pub 1)
  (Date.)
  (HashMap.)
  (set/union #{} #{}))

(defn use-full []
  (clojure.string/upper-case "x")
  (clojure.walk/walk identity identity [1]))
