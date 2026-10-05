(ns nsx.a6
  (:require [clojure.string :as str :refer [join blank? trim upper-case lower-case capitalize split split-lines starts-with? ends-with? includes? replace reverse]]))

(defn f [] (join "" []) (blank? "") (trim "") (upper-case "") (lower-case "") (split "" #"") (includes? "" ""))
