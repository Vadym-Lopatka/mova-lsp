(ns lint.imp-lintas
  (:require [lint.imp-src :as src]
            [lint.imp-user :as u]))

(defn h []
  (u/one 1 2 3)
  (src/one 1 2 3))
