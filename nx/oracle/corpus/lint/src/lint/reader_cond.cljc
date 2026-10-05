(ns lint.reader-cond
  (:require [clojure.string :as str]
            #?(:clj [clojure.java.io :as io]
               :cljs [goog.string :as gs])
            [clojure.set :as set]))

(defn f [x]
  #?(:default (str/join x) :clj (inc x))
  (set/union x x))
