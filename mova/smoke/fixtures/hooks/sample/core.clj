(ns sample.core)

(defmacro my-macro [& args]
  `(do ~@args))
