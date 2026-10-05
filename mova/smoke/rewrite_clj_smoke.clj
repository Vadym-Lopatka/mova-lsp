;; mova/PLAN.md stage 1 smoke: exercises the rewrite-clj + borkdude.rewrite-edn
;; surface clojure-lsp actually calls (grepped lib/src for rewrite-clj / z/ /
;; n/ usage: clojure_lsp.parser, refactor.edit, refactor.transform, and every
;; feature.* namespace that requires rewrite-clj.{node,zip}).
;;
;; Run on the JVM oracle:
;;   cd cli && clojure -M ../mova/smoke/rewrite_clj_smoke.clj > ../mova/smoke/rewrite_clj_smoke.jvm.out
;; Run on Mova:
;;   MOVA_BIN=... mova/bin/lsp-mova mova/smoke/rewrite_clj_smoke.clj
;; Outputs must be byte-identical.
(ns rewrite-clj-smoke
  (:require
   [rewrite-clj.node :as n]
   [rewrite-clj.parser :as p]
   [rewrite-clj.zip :as z]
   [borkdude.rewrite-edn :as redn]))

(defn line [& args]
  (prn (vec args)))

;; ---------------------------------------------------------------------------
;; 1. parser/parse-string-all over a source blob exercising reader macros,
;;    metadata, comments, uneval (#_), regex, namespaced maps and #? forms.
(def src
  "(ns foo.bar\n  (:require [clojure.string :as str]))\n\n;; a leading comment\n(defn ^:private ^String f\n  \"docstring\"\n  [^long a b]\n  #_(ignored form)\n  #\"[a-z]+\"\n  #:foo{:a 1 :bar/b 2}\n  #?(:clj (+ a b) :cljs (- a b))\n  {:a 1 :b [1 2 3] :c #{1 2}})\n")

(def parsed-all (p/parse-string-all src))
(line :parse-string-all-count (count (n/children parsed-all)))
(line :parse-string-all-tags (mapv n/tag (n/children parsed-all)))
(line :parse-string-all-string (n/string parsed-all))
(line :parse-string-all-sexprs
      (mapv (fn [nd] (try (n/sexpr nd) (catch Exception _ :unsexprable)))
            (n/children parsed-all)))

;; ---------------------------------------------------------------------------
;; 2. zip/of-string with track-position?, navigation, position/position-span.
(def zloc (z/of-string src {:track-position? true}))
(line :of-string-tag (z/tag zloc))
(def to-defn (z/find-value zloc z/next 'defn))
(line :find-value-defn (some-> to-defn z/sexpr))
(line :find-value-defn-position (some-> to-defn z/position))
(def fn-name-loc (some-> to-defn z/right))
(line :fn-name-sexpr (some-> fn-name-loc z/sexpr))
(line :fn-name-position-span (some-> fn-name-loc z/position-span))

(def to-map (z/find-tag zloc z/next :map))
(line :find-tag-map-sexpr (some-> to-map z/sexpr))
(line :find-tag-map-string (some-> to-map z/string))

(def down-up (some-> zloc z/down z/up))
(line :down-up-same? (some-> down-up z/sexpr) (some-> zloc z/sexpr))

(def leftmost-loc (some-> zloc z/down z/rightmost z/leftmost))
(line :leftmost-tag (some-> leftmost-loc z/tag))

;; walk every loc via z/next, counting nodes and tags seen.
(defn walk-count [zl]
  (loop [loc zl n 0 tags []]
    (if (z/end? loc)
      [n tags]
      (recur (z/next loc) (inc n) (conj tags (z/tag loc))))))
(let [[cnt tags] (walk-count zloc)]
  (line :walk-count cnt)
  (line :walk-tags tags))

;; ---------------------------------------------------------------------------
;; 3. edit / insert-right / remove, then re-serialize with z/string and
;;    z/root-string.
(def small (z/of-string "[1 2 3]"))
(def edited (-> small z/down (z/replace 99) z/right (z/insert-right 100)))
(line :edited-string (z/string edited))
(line :edited-root-string (z/root-string edited))
(def removed (-> small z/down z/right z/remove))
(line :removed-root-string (z/root-string removed))

;; ---------------------------------------------------------------------------
;; 4. node constructors + n/coerce round-tripping.
(line :token-node (n/string (n/token-node 42)))
(line :keyword-node (n/string (n/keyword-node :foo/bar)))
(line :string-node (n/string (n/string-node "hi\nthere")))
(line :vector-node (n/string (n/vector-node [(n/token-node 1) (n/spaces 1) (n/token-node 2)])))
(line :map-node (n/string (n/map-node [(n/keyword-node :a) (n/spaces 1) (n/token-node 1)])))
(line :list-node (n/string (n/list-node [(n/token-node 'quote) (n/spaces 1) (n/token-node 'x)])))
(line :comment-node (n/string (n/comment-node "; a comment\n")))
(line :meta-node (n/string (n/meta-node (n/keyword-node :private) (n/token-node 'x))))
(line :coerce-symbol (n/string (n/coerce 'foo.bar/baz)))
(line :coerce-vector (n/string (n/coerce [1 2 3])))
(line :coerce-map (n/string (n/coerce {:a 1})))
(line :coerce-keyword (n/string (n/coerce :a/b)))

;; ---------------------------------------------------------------------------
;; 5. borkdude.rewrite-edn: parse-string / assoc-in / update / string,
;;    round-tripping while preserving whitespace/comments.
(def edn-src "{:a 1\n ;; a comment\n :b {:c 2}}\n")
(def enode (redn/parse-string edn-src))
(line :redn-parse-string (n/string enode))
(def enode2 (redn/assoc-in enode [:b :c] 99))
(line :redn-assoc-in (n/string enode2))
(def enode3 (redn/update enode2 :a (fn [nd] (inc (n/sexpr nd)))))
(line :redn-update (n/string enode3))
(def enode4 (redn/assoc enode3 :new-key [1 2 3]))
(line :redn-assoc (n/string enode4))
(line :redn-sexpr (redn/sexpr enode3))
