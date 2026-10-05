(ns app.main
  (:require [app.util :as util]
            [babashka.fs :as fs]
            [babashka.process :as shell]
            [clojure.string :as str]))

(defn clj-files [dir]
  (if (fs/exists? dir)
    (map str (fs/glob dir "**.clj"))
    []))

(defn run-echo [msg]
  (let [unused (str/trim msg)
        proc (shell/process ["echo" msg] {:out :string})]
    (-> @proc :out str/trim util/shout)))

(defn -main [& _args]
  (println (util/to-json {:files (clj-files "src")
                          :echo (run-echo "hi")})))
