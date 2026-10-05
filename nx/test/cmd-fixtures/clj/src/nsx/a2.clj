(ns nsx.a2 (:require [clojure.string :as str] [clojure.set :as set]) (:import java.util.Date))

(defn f [] (str/join "," []) (set/union #{} #{}) (Date.))
