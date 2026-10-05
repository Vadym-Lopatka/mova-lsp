(ns nsx.a3
  (:require
   [clojure.string :as str]
   #?(:clj [clojure.java.io :as io]
      :cljs [goog.string :as gstring])
   #?@(:clj [[clojure.set :as set]
             [clojure.walk :as walk]])
   [clojure.edn :as edn :refer [read-string]])
  #?(:clj (:import [java.util Date UUID])))

(defn f [] (str/join "" []) #?(:clj (io/file "x")) (set/union #{} #{}) (walk/walk identity identity []) (read-string "1") #?(:clj (Date.)))
