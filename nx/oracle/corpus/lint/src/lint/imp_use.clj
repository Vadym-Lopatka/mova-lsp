(ns lint.imp-use
  (:require [lint.imp-user :as u]
            [lint.imp-user :refer [one]]))

(defn g []
  (u/one 1)
  (u/one 1 2)
  (u/two 1 2)
  (u/four 1)
  (u/three-renamed 1)
  (u/nope)
  (one 1 2)
  u/val1)
