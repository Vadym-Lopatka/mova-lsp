(ns app.util
  (:require [clojure.string :as str]
            [cheshire.core :as json]))

(defn shout [s]
  (str/upper-case s))

(defn to-json [m]
  (json/generate-string m))

(defn parse [s]
  (json/parse-string s true))
