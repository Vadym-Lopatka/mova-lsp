(ns my.linter
  (:require [clojure-lsp.custom-linters-api :as api]))

(defn lint [{:keys [uris reg-diagnostic!]}]
  (doseq [uri uris]
    (reg-diagnostic! {:uri uri
                      :level :warning
                      :message "custom linter fired"
                      :source "my-linter"
                      :code "my-code"
                      :range {:row 1 :col 1 :end-row 1 :end-col 2}})))
