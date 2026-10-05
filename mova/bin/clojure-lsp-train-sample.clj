(ns train.sample
  "Training sample for the Mova heap image: common shapes kondo analyzes."
  (:require
   [clojure.set :as set]
   [clojure.string :as string]
   [clojure.test :refer [deftest is testing]])
  (:import
   (java.io File)
   (java.util UUID)))

(set! *warn-on-reflection* true)

(def ^:private cache (atom {}))
(declare helper)
(defonce state (atom nil))

(defprotocol Shape
  (area [this])
  (describe [this opts]))

(defrecord Circle [r]
  Shape
  (area [_] (* Math/PI r r))
  (describe [this {:keys [unit] :or {unit "cm"}}] (str "circle " (area this) unit)))

(deftype Box [w h]
  Shape
  (area [_] (* w h))
  (describe [_ _] "box"))

(defmulti render :kind)
(defmethod render :text [{:keys [text]}] (string/upper-case text))
(defmethod render :default [m] (pr-str m))

(defmacro with-timing [& body]
  `(let [t# (System/nanoTime)] (do ~@body) (- (System/nanoTime) t#)))

(defn- helper
  "Doc."
  ([x] (helper x 1))
  ([x y & more]
   (let [{:keys [a b] :as m} {:a x :b y}
         [p q & rs] more]
     (cond
       (nil? a) :none
       (and (number? a) (pos? b)) (+ a b (count rs))
       :else (merge m {:p p :q q})))))

(defn process [^File f items]
  (let [id (str (UUID/randomUUID))
        xs (->> items
                (map #(assoc % :id id))
                (filter (comp some? :name))
                (remove #(string/blank? (:name %)))
                (group-by :kind))]
    (when-let [path (some-> f .getPath)]
      (swap! cache assoc path xs))
    (if-let [c (get @cache :x)]
      (reduce-kv (fn [acc k v] (assoc acc k (count v))) {} c)
      (for [[k v] xs
            :let [n (count v)]
            :when (pos? n)]
        [k n]))))

(defn walk [m]
  (loop [acc [] [x & xs] (seq m)]
    (if x
      (recur (conj acc (update x 1 inc)) xs)
      (into (sorted-map) acc))))

(defn safe [f & args]
  (try
    (apply f args)
    (catch IllegalArgumentException e (.getMessage e))
    (catch Exception _ nil)
    (finally (reset! state :done))))

(defn dispatch [x]
  (case x
    :a 1
    (:b :c) 2
    (condp = x 3 :three 4 :four :other)))

(defn threads [m]
  (-> m (assoc :k 1) (update :k inc) (dissoc :z) (cond-> (:y m) (assoc :y2 true)) (as-> $ (set/rename-keys $ {:k :kk}))))

(defn side-effects [xs]
  (doseq [x xs :when (odd? x)] (println x))
  (dotimes [i 3] (swap! state (fnil + 0) i))
  (letfn [(ev? [n] (if (zero? n) true (od? (dec n))))
          (od? [n] (if (zero? n) false (ev? (dec n))))]
    (ev? 10))
  (with-timing (mapv inc xs))
  (reify Shape (area [_] 0) (describe [_ _] "reified"))
  (binding [*out* *out*] (format "%s-%d" "a" 1))
  #{:a :b} #"re.*x" \c 1.5 22/7 'sym ::kw ::set/alias-kw)

(deftest process-test
  (testing "process"
    (is (= {} (process nil [])))
    (is (thrown? Exception (throw (ex-info "boom" {:a 1}))))
    (is (= 3 (helper 1 2)))
    (unknown-fn 1)
    (let [unused 1] (inc "x"))))
