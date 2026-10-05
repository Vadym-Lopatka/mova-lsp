;; lsp/io (clojure-lsp-on-Mova campaign, mova/PLAN.md "Stages" #1: "transport
;; (byte stdio, JSON, jsonrpc4clj)"): a tiny JSON-RPC-over-stdio server, run
;; UNMODIFIED on both the JVM and Mova (mova/PLAN.md's oracle rule) via
;; `mova/smoke/drive_jsonrpc.py`.
;;
;;   JVM:  cd cli && clojure -M ../mova/smoke/jsonrpc_echo.clj
;;   Mova: MOVA_BIN=<mova binary> mova/bin/lsp-mova mova/smoke/jsonrpc_echo.clj
;;
;; Built directly on `jsonrpc4clj.io-chan` (the actual transport layer this
;; campaign owns: byte-level Content-Length framing + JSON), NOT on
;; `jsonrpc4clj.io-server`/`jsonrpc4clj.server` -- those pull in `promesa`
;; (explicitly out of scope for this campaign) and `clojure.pprint` (not
;; yet ported to Mova), so this script is its own tiny hand-rolled
;; request/notification dispatcher, exactly the "chan in, chan out" shape
;; `io-chan` hands back either way (see `mova/NOTES.md`'s gap entry).
;;
;; Protocol (driven by `drive_jsonrpc.py`):
;;   request  "initialize" -> fixed capabilities result
;;   request  "echo"       -> echoes `:params` back as the result
;;   request  (other)      -> a `method-not-found` (-32601) error response
;;   notification "notify/trigger" -> sends a SERVER-INITIATED notification
;;     ("server/notified") back on the same connection -- proves the
;;     server can write to the output channel outside a request/response
;;     turn, not just reactively.
;;   notification "exit"   -> stops the read loop and exits.

(ns jsonrpc-echo-smoke
  (:require
   [clojure.core.async :as async]
   [jsonrpc4clj.io-chan :as io-chan]))

(def capabilities
  "A fixed, deterministic `initialize` result -- no timestamps/PIDs/paths,
  so the JVM and Mova runs produce byte-identical JSON once
  `drive_jsonrpc.py` normalizes key order (see that script's doc)."
  {:capabilities {:text-document-sync 1
                  :hover-provider true
                  :completion-provider {:trigger-characters ["." ":"]}}
   :server-info {:name "mova-smoke" :version "0.1.0"}})

(defn handle-request
  [{:keys [id method params]}]
  (case method
    "initialize" {:jsonrpc "2.0" :id id :result capabilities}
    "echo" {:jsonrpc "2.0" :id id :result params}
    {:jsonrpc "2.0"
     :id id
     :error {:code -32601 :message "Method not found" :data {:method method}}}))

(defn run
  []
  (let [in-ch (io-chan/input-stream->input-chan System/in)
        out-ch (io-chan/output-stream->output-chan System/out)]
    (loop []
      (let [msg (async/<!! in-ch)]
        (cond
          ;; input closed (EOF) -- stop.
          (nil? msg) nil
          (= msg :parse-error) (recur)
          ;; a request always carries :id; a notification never does.
          (contains? msg :id)
          (do (async/>!! out-ch (handle-request msg))
              (recur))
          (= (:method msg) "exit") nil
          (= (:method msg) "notify/trigger")
          (do (async/>!! out-ch {:jsonrpc "2.0"
                                 :method "server/notified"
                                 :params {:from "server" :echo (:params msg)}})
              (recur))
          :else (recur))))
    (async/close! out-ch)
    ;; Give the writer thread a moment to flush the last message before
    ;; the process exits -- `output-stream->output-chan`'s writer runs on
    ;; its own thread (see `io_chan`'s doc), and closing `out-ch` only
    ;; signals it to stop, it doesn't block until it has.
    (async/<!! (async/timeout 200))
    (async/close! in-ch)))

(run)
