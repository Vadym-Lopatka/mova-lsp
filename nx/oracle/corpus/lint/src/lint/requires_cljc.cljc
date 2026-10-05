(ns lint.requires-cljc
  (:require [clojure.string :as str]
            [clojure.set :as set]
            #?(:clj [clojure.java.io :as io]
               :cljs [goog.string :as gs])))

(defn f [] (str/join "," []) #?(:clj (io/file "x") :cljs 1))
