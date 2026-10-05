(ns lint.aliased-cljs
  (:require [clojure.string :as str :refer [join]]
            [cljs.core.async :as a :refer [chan put!]]))

(defn f []
  (str/join "," [1])
  (join "," [1])
  (a/put! (a/chan) 1)
  (put! (chan) 1))
