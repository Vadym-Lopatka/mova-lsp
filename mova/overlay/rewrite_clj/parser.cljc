(ns rewrite-clj.parser
  "Parse Clojure/ClojureScript/EDN source code to nodes.

  Parsing includes all source code elements including whitespace.

  After parsing, the typical next step is [[rewrite-clj.zip/edn]] to create zipper.

  Alternatively consider parsing and zipping in one step from [[rewrite-clj.zip/of-string]] or [[rewrite-clj.zip/of-file]]."
  (:require [rewrite-clj.node.comment :refer [comment-node]]
            [rewrite-clj.node.fn :refer [fn-node]]
            [rewrite-clj.node.forms :as nforms]
            [rewrite-clj.node.keyword :refer [keyword-node]]
            [rewrite-clj.node.meta :refer [meta-node raw-meta-node]]
            [rewrite-clj.node.namespaced-map :refer [namespaced-map-node map-qualifier-node]]
            [rewrite-clj.node.quote :refer [quote-node syntax-quote-node unquote-node unquote-splicing-node]]
            [rewrite-clj.node.reader-macro :refer [var-node eval-node reader-macro-node deref-node]]
            [rewrite-clj.node.regex :refer [regex-node]]
            [rewrite-clj.node.seq :refer [list-node map-node vector-node set-node]]
            [rewrite-clj.node.stringz :refer [string-node]]
            [rewrite-clj.node.token :refer [token-node]]
            [rewrite-clj.node.uneval :refer [uneval-node]]
            [rewrite-clj.node.whitespace :refer [whitespace-node newline-node comma-node]]
            [rewrite-clj.parser.core :as p]
            [rewrite-clj.reader :as reader]
            ;; MOVA-PATCH (mova/PLAN.md, lsp/reader round 3): native CST
            ;; parser, src/builtins/cst_reader.rs. This ctors map has a
            ;; `:whitespace` entry, which is exactly what tells the Rust
            ;; side to run in TRIVIA mode (whitespace/newline/comma/
            ;; comment become real nodes, `^`/`#^`/`#_` are ordinary
            ;; ctor'd nodes) instead of the clj-kondo-fork mode.
            [mova.reader]))

#?(:clj (set! *warn-on-reflection* true))

;; MOVA-PATCH (lsp/reader round 3): tag keyword -> this library's OWN
;; node ctor, called bottom-up by the Rust parser. Any tag missing here
;; (or any construct the Rust side doesn't recognize) makes the native
;; call return `:mova.reader/fallback` and the functions below re-parse
;; with the original interpreted parser.
(def ^:private native-ctors
  {:token token-node
   :keyword keyword-node
   :string string-node
   :list list-node
   :vector vector-node
   :map map-node
   :set set-node
   :fn fn-node
   :quote quote-node
   :syntax-quote syntax-quote-node
   :unquote unquote-node
   :unquote-splicing unquote-splicing-node
   :deref deref-node
   :var var-node
   :eval eval-node
   :regex regex-node
   :namespaced-map namespaced-map-node
   :map-qualifier map-qualifier-node
   :reader-macro reader-macro-node
   :meta meta-node
   :raw-meta raw-meta-node
   :uneval uneval-node
   :whitespace whitespace-node
   :newline newline-node
   :comma comma-node
   :comment comment-node
   :forms nforms/forms-node})

;; ## Parser Core

(defn ^:no-doc parse
  "Parse next form from the given reader."
  [#?(:cljs ^not-native reader :default reader)]
  (p/parse-next reader))

(defn ^:no-doc parse-all
  "Parse all forms from the given reader."
  [#?(:cljs ^not-native reader :default reader)]
  (let [nodes (->> (repeatedly #(parse reader))
                   (take-while identity))
        position-meta (merge (meta (first nodes))
                             (select-keys (meta (last nodes))
                                          [:end-row :end-col]))]
    (with-meta (nforms/forms-node nodes) position-meta)))

;; ## Specialized Parsers

(defn parse-string
  "Return a node for first source code element in string `s`."
  [s]
  ;; MOVA-PATCH (lsp/reader round 3): native path first;
  ;; `:mova.reader/fallback` (any unsupported construct or lex/parse
  ;; error) re-parses with the original interpreted reader/parser so
  ;; output and error messages/positions stay byte-identical.
  (let [native (mova.reader/parse-string s native-ctors)]
    (if (= native :mova.reader/fallback)
      (parse (reader/string-reader s))
      native)))

(defn parse-string-all
  "Return forms node for all source code elements in string `s`."
  [s]
  ;; MOVA-PATCH (lsp/reader round 3): native path first, same fallback
  ;; contract as `parse-string` above.
  (let [native (mova.reader/parse-string-all s native-ctors)]
    (if (= native :mova.reader/fallback)
      (parse-all (reader/string-reader s))
      native)))

#?(:clj
   (defn parse-file
     "Return node for first source code element in file `f`."
     [f]
     ;; MOVA-PATCH (lsp/reader round 3): route through the native-
     ;; accelerated `parse-string` instead of a streaming file reader --
     ;; callers only ever want the fully-realized AST, so slurping first
     ;; costs nothing semantically.
     (parse-string (slurp f))))

#?(:clj
   (defn parse-file-all
     "Return forms node for all source code elements in file `f`."
     [f]
     (parse-string-all (slurp f))))
