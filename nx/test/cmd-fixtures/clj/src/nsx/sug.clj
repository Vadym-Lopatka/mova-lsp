(ns nsx.sug
  (:require [clojure.string :as str]
            [clojure.set :as set])
  (:import [java.util Date]))

(defn f []
  (str/join "," [])
  (walk/postwalk identity 1)
  (clojure.edn/read-string "1")
  (join "a" [])
  (io/file "x")
  (UUID/randomUUID)
  (Date.)
  (deftest x (is 1)))

(defn g [x]
  (let [a (inc x)
        b (dec x)]
    (+ a b (* a b))))
