(ns lint.unresolved-cljs
  (:require [clojure.string :as str]))

(defn f []
  (undefined-fn 1)
  (str/nonexistent "x")
  (js/console.log 1)
  js/window
  (nons/foo 1)
  (.-foo js/window)
  (Foo. 1))
