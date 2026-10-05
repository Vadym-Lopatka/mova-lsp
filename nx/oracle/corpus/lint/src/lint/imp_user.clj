(ns lint.imp-user
  (:require [lint.imp-src :as src]
            [lint.imp-src2]
            [potemkin :refer [import-vars import-fn import-macro import-def]]))

(import-vars
 [lint.imp-src one two mac]
 [src multi]
 [lint.imp-src2
  :refer [three]
  :rename {three three-renamed}]
 lint.imp-src/val1
 [lint.imp-src2 four missing-var])

(import-fn lint.imp-src/two two-fn)
(import-macro lint.imp-src/mac)
(import-def lint.imp-src/val1 val-copy)

(defn f []
  (one 1)
  (one 1 2)
  (two 1 2)
  (two 1)
  (mac 1)
  (multi)
  (multi 1 2)
  (three-renamed 1 2 3)
  (three-renamed 1)
  (four)
  (four 1)
  (missing-var)
  (two-fn 1 2)
  (two-fn 1)
  val1
  val-copy)
