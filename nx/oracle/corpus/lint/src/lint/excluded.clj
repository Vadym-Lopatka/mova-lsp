(ns lint.excluded
  (:refer-clojure :exclude [update nothing-core get])
  (:require [lint.helper :as h]))

(defn update [m] m)
(defn g [] (h/pub 1))
(declare g)
(declare later later)
(declare update)
(defn later [] 1)
(declare later)
