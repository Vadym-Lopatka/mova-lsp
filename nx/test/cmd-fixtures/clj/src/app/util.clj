(ns app.util
  (:import [java.util Date UUID]))

(defprotocol Greeter
  (greet [this]))

(defrecord Person [name]
  Greeter
  (greet [_] (str "hi " name)))

(defmacro twice [x]
  `(do ~x ~x))

(defn now [] (Date.))

(defn fresh-id [] (str (UUID/randomUUID)))
