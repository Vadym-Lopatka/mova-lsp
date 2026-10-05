(ns lint.defs2
  (:require [clojure.set :as set]
            [clojure.set :as set2]
            [clojure.string :as s]
            [clojure.string :as s]
            [clojure.walk]
            [clojure.walk :as w]
            [lint.defs2 :as self]
            lint.defs2
            [lint.defs2 :refer [g]])
  (:import (java.util Date Date List)
           java.util.Date
           (java.io File)
           [java.util Map]))

(def a (fn [x] x))
(def b (fn b2 [x] x))
(def ^:private c (let [y 1] (fn [x] (+ x y))))
(def d (comp inc inc))
(def e #(inc %))
(def ^{:doc "d"} h (fn [] 1))
(defonce o (fn [] 1))
(def p "doc" (fn [] 1))
(let [z (fn [] 1)] z)
(declare g)
(defn g [] 1)
(defn g [] 2)
(def g 3)
(defn map [] 1)
(def inc 1)
(defn str [x] x)
(defmacro when [x] x)
(defn ^String vec-hint [] "a")
(defn arg-hint ^String [x] "a")
(defn ^String multi-hint ([] "a") ([x] "b"))
(defn ^long prim-hint [x] 1)
(defn ^"[B" arr-hint [x] 1)
(defn ^java.util.Map fq-hint [x] 1)
(defn ^{:tag String} tag-hint [x] 1)
(defn ^:foo kw-hint [x] 1)
(defn x1 {:tag String} [x] 1)
(def ^String v 1)
(defn x2 ^String [x] 1)
(defn x3 ^long ^String [x] 1)
(def ^{:deprecated true} old1 1)
(defn use-old [] (lint.helper/old))
(Date.)
