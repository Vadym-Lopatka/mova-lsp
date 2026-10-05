(ns app.common)

(defn now-ms []
  #?(:clj (System/currentTimeMillis)
     :cljs (.getTime (js/Date.))))

(defn platform []
  #?(:clj :jvm
     :cljs :js))
