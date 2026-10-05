;; mova/PLAN.md clj-kondo/clojure-lsp on-disk cache campaign:
;; `mova.transit/write-json` (src/builtins/transit.rs) +
;; `cognitect.transit/writer`+`write` (mova/shims/cognitect/transit.mova).
;; Writes a fixed list of representative values (scalars, a cache-code
;; back-reference case, and a small clj-kondo-shaped cache-entry map) to
;; a temp file through the real `writer`/`write` shim, then slurps and
;; prints the transit-json text -- byte-for-byte diffable against the
;; JVM oracle.
;;
;; Every map/vector below stays AT OR UNDER 8 entries on purpose: Mova's
;; `PMap` only preserves LITERAL insertion order in its `Small` (<=8
;; entries) representation (src/value.rs `PMAP_SMALL_MAX`), matching
;; Clojure's own `PersistentArrayMap` threshold -- past that both sides
;; promote to a genuine hash map/CHAMP trie whose iteration order is
;; NOT guaranteed to match between the two runtimes (different hash
;; algorithms), so a real, larger clj-kondo cache entry is exercised
;; separately below for MOVA-SIDE round-trip only (not byte-diffed).
;;
;; Oracle: `cd cli && clojure -M ../mova/smoke/transit_smoke.clj > ../mova/smoke/transit_smoke.jvm.out`
;; Mova:   `MOVA_BIN=... mova/bin/lsp-mova mova/smoke/transit_smoke.clj`
(ns mova.smoke.transit-smoke
  (:require
   [clojure.java.io :as io]
   [cognitect.transit :as transit]))

(defn write-str [v]
  (let [f (java.io.File/createTempFile "transit-smoke" ".json")]
    (try
      (with-open [os (io/output-stream f)]
        (transit/write (transit/writer os :json) v))
      (slurp f)
      (finally (.delete f)))))

(defn read-str
  "Portable inverse of `write-str` -- round-trips `s` back through a temp
  file rather than an in-memory byte stream, since `java.io.
  ByteArrayInputStream` has no Mova host-class shim (review round 2)."
  [s]
  (let [f (java.io.File/createTempFile "transit-smoke" ".json")]
    (try
      (spit f s)
      (with-open [is (io/input-stream f)]
        (transit/read (transit/reader is :json)))
      (finally (.delete f)))))

(def scalars
  [nil true false 0 42 -7 3.14 \a
   "hello" "~tricky" "^caret" "`tick"
   :foo :some-ns/foo
   'bar 'some-ns/bar
   '(1 2 3) [1 2 :a "b"]])

(doseq [v scalars]
  (println (write-str v)))

;; A set is deliberately NOT exercised here: its wire ELEMENT ORDER is a
;; hash-bucket artifact (Mova's `champ` set and the JVM's
;; `PersistentHashSet` use different hash functions/layouts, so
;; iteration order genuinely diverges between the two runtimes) and
;; would never byte-match this smoke's golden -- `write_json_tests` in
;; transit.rs covers set round-tripping functionally instead.
(assert (= #{:a :b :c} (read-str (write-str #{:a :b :c}))) "set round-trip (order-independent)")

;; A cache-code back-reference case: the same long-enough keyword key
;; repeated across two small (array-)maps in one write -- the second
;; occurrence must come back as a `^<code>` reference, matching every
;; vendored built-in `.transit.json`'s own caching (transit.rs module
;; doc).
(println (write-str [{:filename "src/foo.clj"} {:filename "src/foo.clj"}]))

;; A small, real clj-kondo-shaped cache entry (<=8 keys, byte-diffable).
(println (write-str {:filename "src/foo.clj"
                     :ns 'foo.core
                     :row 1
                     :col 1
                     :source :built-in}))

;; A real clj-kondo built-in cache entry, read straight off disk and
;; round-tripped through write+read (MOVA-SIDE ONLY -- likely >8 keys,
;; so not byte-diffed against the JVM oracle, see module doc above):
;; confirms the writer handles genuine, large, nested analysis maps.
(let [resource (io/resource "clj_kondo/impl/cache/built_in/clj/clojure.core.transit.json")
      original (with-open [is (io/input-stream resource)]
                 (transit/read (transit/reader is :json)))
      round-tripped (read-str (write-str original))]
  (println "clojure.core cache entry round-trip ok?" (= original round-tripped)))
