(ns lint.imports2
  (:import (java.util Date List Map)
           (java.io File)
           [java.net URL URI]
           java.text.SimpleDateFormat
           (java.util.concurrent TimeUnit)
           (java.lang String)
           clojure.lang.PersistentVector))

(defn f []
  (Date.)
  (let [^List l nil] l)
  (URL. "x")
  TimeUnit/SECONDS
  (instance? PersistentVector 1))
