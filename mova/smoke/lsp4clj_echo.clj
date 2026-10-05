;; lsp/host (clojure-lsp-on-Mova campaign, mova/PLAN.md): the REAL LSP
;; server engine -- `jsonrpc4clj.server`'s `ChanServer` (`receive-request`/
;; `receive-notification` multimethods, `send-notification`, `send-
;; request`, `shutdown`) plus `jsonrpc4clj.io-server/stdio-server` --
;; running UNMODIFIED on both the JVM and Mova, exactly like
;; `jsonrpc_echo.clj` (this campaign's earlier, hand-rolled `io-chan`-only
;; smoke test) but one layer up the real stack. `lsp4clj.server` does NOT
;; exist as a namespace in lsp4clj 2.0.1 -- vendored `lsp4clj` is only
;; `coercer`/`lsp.errors`/`lsp.requests` now; the real `ChanServer`/
;; `receive-request` engine lives in `jsonrpc4clj.server`, and real
;; clojure-lsp's own `cli/src/clojure_lsp/server.clj` requires exactly
;; that (`jsonrpc4clj.server`/`jsonrpc4clj.io-server`/`lsp4clj.coercer`,
;; no `lsp4clj.server`) -- see `mova/NOTES.md`.
;;
;;   JVM:  cd cli && clojure -M ../mova/smoke/lsp4clj_echo.clj
;;   Mova: MOVA_BIN=<mova binary> mova/bin/lsp-mova mova/smoke/lsp4clj_echo.clj
;;
;; Protocol (driven by `drive_jsonrpc.py`):
;;   request  "initialize" -> fixed capabilities result
;;   request  "echo"       -> echoes `:params` back as the result
;;   request  "will-throw" -> throws synchronously -> a real JSON-RPC
;;     "Internal error" (-32603) response, produced by `jsonrpc4clj.
;;     server` ITSELF (this smoke test's handler does nothing special)
;;   notification "notify/trigger" -> server sends a SERVER-INITIATED
;;     request ("server/ping") to the client and awaits the reply
;;     (`send-request`/`deref`, real `PendingRequest`), then a
;;     notification ("server/notified") with the reply folded in --
;;     proves the server->client REQUEST direction works, not just
;;     notifications.
;;   request  "shutdown"   -> nil result (LSP convention)
;;   notification "exit"   -> `jsonrpc4clj.server/shutdown`, then the
;;     process exits (code 0) -- clean-shutdown check.

(ns lsp4clj-echo-smoke
  (:require
   [clojure.core.async :as async]
   [jsonrpc4clj.io-server :as io-server]
   [jsonrpc4clj.server :as server]))

(def capabilities
  {:capabilities {:text-document-sync 1 :hover-provider true}
   :server-info {:name "mova-smoke" :version "0.1.0"}})

(defmethod server/receive-request "initialize" [_method _context _params]
  capabilities)

(defmethod server/receive-request "echo" [_method _context params]
  params)

(defmethod server/receive-request "will-throw" [_method _context _params]
  (throw (ex-info "deliberate smoke-test failure" {:reason "boom"})))

(defmethod server/receive-request "shutdown" [_method _context _params]
  nil)

(defmethod server/receive-notification "notify/trigger" [_method context params]
  (let [srv (:server context)
        reply (server/send-request srv "server/ping" {:tag (:tag params)})]
    (server/send-notification srv "server/notified"
                              {:from "server" :ping-reply @reply})))

(defmethod server/receive-notification "exit" [_method context _params]
  (server/shutdown (:server context))
  ;; `shutdown`'s `join` is delivered by the cleanup `go` block right
  ;; after it closes `output-ch` -- BEFORE the output writer thread is
  ;; guaranteed to have drained and flushed whatever was still queued on
  ;; it (a real race in jsonrpc4clj.server itself, not mova-specific: a
  ;; client that pipelines requests immediately followed by `exit`,
  ;; without waiting for every response first, can race the last
  ;; write). A short grace wait here is the practical fix a real server
  ;; would also want before hard-exiting.
  (async/<!! (async/timeout 200))
  (System/exit 0))

(defn run
  []
  ;; `chan-server`'s own default `:clock` (`(java.time.Clock/
  ;; systemDefaultZone)`) now works on both the JVM and Mova -- no
  ;; override needed (a `proxy`-based stand-in used to be here; measured
  ;; on the JVM to throw `ClassCastException` on an async thread,
  ;; silently, since `ChanServer`'s `clock` field is REAL-`java.time.
  ;; Clock`-type-hinted -- see `mova/NOTES.md`).
  (let [srv (io-server/stdio-server {:in System/in :out System/out})
        context {:server srv}]
    (deref (server/start srv context))))

(run)
