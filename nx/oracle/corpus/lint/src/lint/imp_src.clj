(ns lint.imp-src)

(defn one [a] a)
(defn two [a b] [a b])
(defn- hidden [] 1)
(defmacro mac [a] a)
(def val1 1)
(defn multi ([] 0) ([a] a))
