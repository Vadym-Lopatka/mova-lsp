(ns mv.a-test
  (:require [clojure.test :refer [deftest is]]
            [mv.a :as a]))

(deftest plain-test
  (is (= 2 (a/plain 1))))
