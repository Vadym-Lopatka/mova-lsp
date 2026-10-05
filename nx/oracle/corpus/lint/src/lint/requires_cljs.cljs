(ns lint.requires-cljs
  (:require [clojure.string :as str]
            [clojure.set :as set :refer [union]]
            [goog.string :as gstring]
            [goog.string.format]
            [cljs.test :refer-macros [deftest is]]
            [lint.helper :as h])
  (:require-macros [lint.helper :refer [mac]])
  (:import [goog.events EventType]))

(defn f [] (str/join "," []))
