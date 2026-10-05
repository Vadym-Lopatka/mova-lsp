(ns app.shapes
  (:require [goog.string :as gstring]))

(defprotocol Shape
  (area [this])
  (label [this]))

(defrecord Circle [r]
  Shape
  (area [_] (* js/Math.PI r r))
  (label [this] (gstring/format "circle %.1f" (area this))))

(defrecord Rect [w h]
  Shape
  (area [_] (* w h))
  (label [this] (str "rect " (area this))))
