(ns lint.round3
  (:require [clojure.core.async :as async]
            [clojure.string
             [split :as s]]
            [clojure.set :as set])
  (:import (java.util regex.Pattern List)
           [java.io File]
           java.lang.reflect.Constructor))

(defn red [f xs] (reduce f xs))
(defn red2 [xs] (reduce + xs))
(defn red3 [xs] (reduce #(conj %1 %2) [] xs))

(defn alts1 [c t]
  (async/alt!! [c t] ([v ch] [v ch]) (async/timeout 10) :t))

(defn alts2 [c]
  (async/alt! c ([v] v) (async/timeout 10) ([x] x)))
