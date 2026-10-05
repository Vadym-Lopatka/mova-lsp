(ns app.extra
  (:require [clojure.string :as str]
            [clojure.set :refer [union difference]]
            [clojure.walk :as walk]
            [app.util :as u]
            [clojure.data :refer :all])
  (:import [java.util Date]))

(def ^:private config {:a 1 :b 2 :c {:d 3}})

(defn- helper [x y]
  (let [a (inc x)
        b (dec y)]
    (-> a (+ b) (* 2) str)))

(defn calc [{:keys [a b] :as m} n]
  (when (pos? n)
    (if (> a b)
      (get-in m [:c :d])
      (get m :x 0))))

(defn looper [xs]
  (for [x xs
        :let [y (* x 2)]
        :when (even? y)]
    (+ x y)))

(defn destr [m]
  (let [v (:a m)
        w (:b m)]
    (+ v w)))

(defn cnd [x]
  (cond
    (< x 0) :neg
    (= x 0) :zero
    :else :pos))

(defn nested [x]
  (if (pos? x)
    :p
    (if (neg? x) :n :z)))

(defn anon [xs]
  (map #(+ % 1) xs)
  (map (fn [a] (* a 2)) xs)
  (filter even? (map inc xs)))

(defmulti disp :type)
(defmethod disp :a [m] (:a m))

(defn kws []
  [::local :app.extra/full {:a/b 1 :a/c 2}
   #:x{:y 1 :z 2}])

(defn threaded [m]
  (->> m (map inc) (filter even?) (reduce +)))

(defn uses []
  (str/join "," (map str (union #{1} #{2})))
  (unknown-fn 1 2)
  (missing/thing 1)
  (Date.)
  (UUID/randomUUID))

(comment
  (helper 1 2)
  (case 1 1 :a 2 :b :c))
