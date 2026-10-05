(ns app.core
  (:require [app.common :as common]
            [app.shapes :as shapes]
            [goog.object :as gobj]
            [goog.string :as gstring]))

(defn describe [s]
  (let [unused (count s)]
    (str (shapes/label s) " " (.-length (str s)))))

(defn ^:export main []
  (let [shapes [(shapes/->Circle 2) (shapes/->Rect 3 4)]
        o #js {:t (common/now-ms)}]
    (gobj/set o "n" (count shapes))
    (js/console.log (gstring/trim " hi ") (js/Date.) o)
    (doseq [s shapes]
      (js/console.log (describe s)))
    (map inc (range 3))))
