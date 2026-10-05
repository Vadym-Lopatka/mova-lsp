(ns hooks.my-hook
  (:require [clj-kondo.hooks-api :as api]))

(defn hook [{:keys [node]}]
  (api/reg-finding! (assoc (meta node)
                           :message "custom hook fired"
                           :type :my-custom-hook
                           :level :warning))
  {:node node})
