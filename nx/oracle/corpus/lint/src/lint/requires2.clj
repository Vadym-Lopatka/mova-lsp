(ns lint.requires2
  (:require [clojure.string :as s]
            [clojure.set :as set :refer [union]]
            [clojure.core.async :as a :refer [go chan]]
            [clojure.test :as t :refer [deftest is]]
            [clojure.pprint :as pp]
            [lint.helper :as help :refer [mac]]
            [lint.helper :as other])
  (:use [clojure.java.io :only [file]]
        clojure.walk)
  (:refer-clojure :exclude [update])
  (:import [java.util.concurrent TimeUnit]))

(defn a-fn [] (s/join "," []))
(defn b-fn [] (union #{} #{}))
(defn update [m] m)
(deftest x (is true))
