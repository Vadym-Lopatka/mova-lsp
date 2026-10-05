;; mova/PLAN.md task 4: exercises clojure_lsp.shared's filename->uri /
;; uri->filename / uri->path / relativize-filepath on file: URIs, incl.
;; spaces and non-ASCII paths.
;;
;; Run on the JVM oracle:
;;   cd cli && clojure -M ../mova/smoke/uri_smoke.clj > ../mova/smoke/uri_smoke.jvm.out
;; Run on Mova:
;;   MOVA_BIN=... mova/bin/lsp-mova mova/smoke/uri_smoke.clj
;; Outputs must be identical.
(ns uri-smoke
  (:require
   [clojure-lsp.shared :as shared]
   [clojure.java.io :as io]
   [clojure.string :as string]))

(defn line [& args]
  (prn (vec args)))

;; root-relative normalization (same find-repo-root trick as kondo_smoke,
;; oracle runs from cli/, Mova from the repo root) so the golden stays
;; portable across checkouts once a real on-disk fixture is involved.
(defn- find-repo-root [start]
  (loop [dir (.getCanonicalFile start)]
    (cond
      (.exists (io/file dir "mova" "PLAN.md")) dir
      (nil? (.getParentFile dir)) (throw (ex-info "uri_smoke: could not find repo root" {}))
      :else (recur (.getParentFile dir)))))
(def repo-root (.getCanonicalPath (find-repo-root (io/file (System/getProperty "user.dir")))))
(defn norm [s] (string/replace (str s) repo-root "<root>"))

(def db {})

(def paths
  ["/home/user/project/core.clj"
   "/home/user/project/a b/core.clj"
   "/home/user/project/wörld/ünïcode.clj"
   "/tmp/has#hash&amp/percent%file.clj"])

(doseq [p paths]
  (let [uri (shared/filename->uri p db)]
    (line :filename->uri p uri)
    (line :uri->filename uri (shared/uri->filename uri))
    (line :uri->path uri (str (shared/uri->path uri)))))

(line :relativize-prefix (shared/relativize-filepath "/home/user/project/a b/core.clj" "/home/user/project"))
(line :relativize-non-prefix (shared/relativize-filepath "/other/dir/file.clj" "/home/user/project"))
(line :conform-uri-scheme (shared/conform-uri-scheme "file:/home/user/project/core.clj"))

;; jar-nested filenames: exercises filename->uri/uri->filename's zipfile:
;; and jar:file: branches (java.net.URI.getPath migration). Uses a real
;; on-disk fixture -- uri->canonical-path needs an existing path to give
;; the same answer as the JVM oracle (both fall back differently on a
;; nonexistent one).
(def fixture-jar (.getCanonicalPath (io/file repo-root "mova" "smoke" "fixtures" "fake.jar")))
(def jar-filename (str fixture-jar ":some/Entry.class"))
(let [zip-uri (shared/filename->uri jar-filename db)]
  (line :jar-filename->uri-zipfile (norm jar-filename) (norm zip-uri))
  (line :zip-uri->filename (norm zip-uri) (norm (shared/uri->filename zip-uri))))
(line :jar-filename->uri-jarfile (norm jar-filename)
      (norm (shared/filename->uri jar-filename {:settings {:dependency-scheme "jar"}})))

;; normalize-uri-from-client: exercises the java.net.URI.getScheme/.getPath migration.
(line :normalize-uri-from-client (shared/normalize-uri-from-client "file:/home/user/project/core.clj"))
