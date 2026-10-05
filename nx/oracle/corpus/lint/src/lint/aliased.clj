(ns lint.aliased
  (:refer-clojure :exclude [into map])
  (:require [clojure.core.async :refer :all :as a]
            [clojure.string :as str :refer [join trim]]
            [lint.helper :as h :refer [pub two]]))

(defn f []
  (a/into [] (a/chan))
  (a/map inc [1])
  (str/join "," [1])
  (join "," [1])
  (trim " a ")
  (str/trim " a ")
  (h/pub 1)
  (pub 1)
  (h/two 1 2)
  (two 1 2)
  (map h/pub [1]))
