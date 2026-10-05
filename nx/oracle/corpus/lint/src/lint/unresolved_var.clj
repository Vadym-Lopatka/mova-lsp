(ns lint.unresolved-var
  (:require [clojure.string :as str]
            [lint.helper :as h :refer [pub]]))

(defn f []
  (h/missing-var 1)
  (str/missing 1)
  (clojure.set/union #{} #{})
  (h/pub 1)
  (h/priv 1)
  #'h/missing2
  h/missing3)
