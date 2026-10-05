(ns lint.some-other-name
  (:require [clojure.string :as str]))

(defn f [x] (str/upper-case x))

(ns lint.second-ns)
