(ns lint.hk)

(defmacro one-of [x elements]
  `(case ~x ~(seq elements) true false))
