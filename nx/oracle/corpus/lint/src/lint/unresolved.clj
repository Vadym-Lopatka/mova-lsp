(ns lint.unresolved
  (:require [clojure.string :as str]
            [lint.helper :as h]))

(defn f []
  (undefined-fn 1)
  undefined-sym
  (str/nonexistent "x")
  (h/nonexistent)
  (nons/foo 1)
  nons/bar
  (clojure.string/bogus 1)
  (clojure.nope/x 1)
  (Foo. 1)
  (Foo/bar)
  (java.util.Foo/x)
  (.method nil)
  java.lang.String
  Unknown)

(defn g [x]
  (let [a 1]
    (+ a b)))

(defn h []
  (declare-me)
  (lint.helper/pub 1)
  (lint.helper/nothing))

(declare later-fn)
(defn use-later [] (later-fn 1))
(defn dyn [] (binding [*dyn* 1] 2))
(defn macro-locals []
  (let [m {:a 1}]
    (-> m :a undefined-in-thread)))
(defn in-quote [] '(undefined-quoted x) `(undefined-syntax-quoted))
(defn in-comment []
  (comment (undefined-in-comment)))
(defn spec-ish [] (try 1 (catch Exception e (println e))))
(defn recur-ok [n] (if (pos? n) (recur (dec n)) n))
(defn this-is [] (reify Object (toString [this] (str this))))
(defn for-ok [] (for [x [1] :let [y x] :when y] y))
(defn fully-qualified-java [] (java.util.Date.) (java.util.Collections/emptyList))
(defn static [] (Math/abs 1) Integer/MAX_VALUE (System/getProperty "x"))
(defn static-missing [] (String/nothing))
(defmulti mm :a)
(defmethod mm :x [m] (undefined-in-mm m))
(defn use-ns-alias [] ::h/foo ::nope/foo)
(defn sym-meta [] ^{:a undefined-meta} [])
(defn with-fn [] (fn [x] (undefined-in-fn x)))
(defn anon [] #(undefined-in-anon %))
(defn cond-thread [x] (cond-> x true (undefined-cond-thread 1)))
