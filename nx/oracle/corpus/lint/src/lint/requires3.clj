(ns lint.requires3
  (:require [clojure.string :as str]
            [clojure.set :as set])
  (:import [java.util Date]))

(require '[clojure.walk :as walk])
(require '[clojure.data :as data :refer [diff]])
(import '[java.io File])
(defn f [] (walk/walk identity identity []))
(defn g [] (str/trim "x"))
