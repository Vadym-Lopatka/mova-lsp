(ns ^{:clj-kondo/config '{:linters {:unresolved-symbol {:exclude [ctx this]}
                                    :unused-binding {:level :off}}}}
    lint.localcfg)

(defn a [x]
  (let [unused 1] (foo ctx this x)))

(defn b [x] (undefined-here x))
