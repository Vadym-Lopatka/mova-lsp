"""Unit tests for run.py normalization + early rules: python3 -m unittest (from mova/lsp-e2e)."""
import unittest
from pathlib import Path
import run
import dep

R = Path("/tmp/proj")
N = run.Norm(R, ["src/a.clj"], "/m")
JAR, PO, MOVA, RS = "jar:x.jar!/a.clj:3", "project-other:x", "mova:core/core.mova:5", "mova:src/b.rs:9"


class Normalize(unittest.TestCase):
    def test_loc(self):
        loc = {"uri": "file:///tmp/proj/src/a.clj", "range": {"start": {"line": 4, "character": 1}}}
        self.assertEqual(N.value("definition", [loc]), "project:src/a.clj:4")
        self.assertEqual(N.value("definition", None), None)
        self.assertEqual(N.origin("project:src/z.clj:1"), "project-other")
        self.assertEqual(N.origin("jdk:java.base/A.java:1"), "jdk")

    def test_mova_origin(self):
        self.assertEqual(N.origin(MOVA), "mova")
        self.assertEqual(N.origin(RS), "mova-native")
        loc = {"uri": "file:///m/core/core.mova", "range": {"start": {"line": 5}}}
        self.assertEqual(N.value("definition", loc), "mova:core/core.mova:5")

    def test_hover_completion(self):
        self.assertEqual(N.value("hover", {"contents": {"value": "\n a \nb"}}), "a")
        self.assertEqual(N.value("completion", {"items": [{"label": "b"}, {"label": "a"}]}), ["a", "b"])

    def test_no_negative_pass(self):
        self.assertEqual(run.summary_row("p", "c", "early", 3, 10, 1)[4], 0)
        self.assertEqual(run.summary_row("p", "c", "early", 10, 4, 1)[4], 6)


class EarlyRules(unittest.TestCase):
    def ok(self, key, got, want, gold, late=("jar", "project-other", "mova")):
        return run.early_ok(key, got, want, gold, N, list(late))

    def test_definition(self):
        self.assertTrue(self.ok("f:1:1:definition", None, JAR, {}))
        self.assertFalse(self.ok("f:1:1:definition", None, "jdk:A.java:1", {}))
        self.assertFalse(self.ok("f:1:1:definition", "project:x:1", JAR, {}))
        self.assertFalse(self.ok("f:1:1:definition", None, RS, {}))  # Rust natives not late_ok
        self.assertTrue(self.ok("f:1:1:definition", None, MOVA, {}))

    def test_hover_completion_follow_definition(self):
        g = {"f:1:1:definition": JAR}
        self.assertTrue(self.ok("f:1:1:hover", None, "h", g))
        self.assertTrue(self.ok("f:1:1:completion", [], ["a"], g))
        self.assertTrue(self.ok("f:1:1:completion", ["b"], ["a"], g))  # partial list, late_ok def
        g = {"f:1:1:definition": "project:src/a.clj:1"}
        self.assertFalse(self.ok("f:1:1:completion", [], ["a"], g))

    def test_hover_rule(self):
        # jar / other-file definition: hover skipped early (any value passes)
        self.assertTrue(self.ok("src/a.clj:1:1:hover", "other", "h", {"src/a.clj:1:1:definition": JAR}))
        self.assertTrue(self.ok("src/a.clj:1:1:hover", "x", "h", {"src/a.clj:1:1:definition": "project:src/b.clj:3"}))
        # jdk and same-file definitions: compared exactly
        for d in ("jdk:java.base/A.java:1", "project:src/a.clj:3"):
            g = {"src/a.clj:1:1:definition": d}
            self.assertFalse(self.ok("src/a.clj:1:1:hover", None, "h", g))
            self.assertTrue(self.ok("src/a.clj:1:1:hover", "h", "h", g))
        # macro-call hover needs the late var: skipped even for same file
        g = {"src/a.clj:1:1:definition": "project:src/a.clj:3", "src/a.clj:1:1:hover": "calling: (x [a])"}
        self.assertTrue(self.ok("src/a.clj:1:1:hover", "calling: (x)", "calling: (x [a])", g))

    def test_references(self):
        self.assertTrue(self.ok("f:1:1:references", [], [JAR], {}))
        self.assertTrue(self.ok("f:1:1:references", ["p"], ["p", JAR], {}))
        self.assertFalse(self.ok("f:1:1:references", [], ["project:src/a.clj:1"], {}))

    def test_split_merge(self):
        e = {"name": "p", "cache": "cold", "phase": "early", "ms": {"early": 5}, "early": {"k": 1}, "early_end_at": 9}
        t = {"name": "p", "cache": "cold", "phase": "settled", "ms": {"settled": 7}, "settled": {"k": 2}, "bg_signal": True}
        [r] = run.merge([e, t])
        self.assertEqual((r["early"], r["settled"], r["ms"], r["bg_signal"]), ({"k": 1}, {"k": 2}, {"early": 5, "settled": 7}, True))

    def test_compare_subset(self):
        fs = run.compare("p", "c", "settled", {"a": 1, "extra": 2}, {"a": 1}, set(), N, [], True)
        self.assertEqual(fs, [])



class JarChecks(unittest.TestCase):
    def test_targets_and_text(self):
        u = "jar:file:///m/clojure.jar!/clojure/core.clj"
        d = lambda l: [{"uri": u, "range": {"start": {"line": l, "character": 6}}}]
        raw = [("a:0:1:definition", "textDocument/definition", d(4)), ("a:0:1:hover", "textDocument/hover", d(4)),
               ("a:1:1:definition", "textDocument/definition", d(4)), ("a:2:1:definition", "textDocument/definition", d(9)),
               ("a:3:1:definition", "textDocument/definition", [{"uri": "file:///x.clj"}])]
        t = run.jar_targets(raw, lambda k: "clojure.core/str", 3)
        self.assertEqual(t, [(u, 4, 6, "str"), (u, 9, 6, "str")])
        txt = "\n\n\n\n(defn str [x])\n"
        self.assertIsNone(run.check_dep_text(txt, 4, "str"))
        self.assertIn("lacks", run.check_dep_text(txt, 0, "str"))
        self.assertIn("got=", run.check_dep_text(None, 0, "str"))
        self.assertIn("past end", run.check_dep_text("x", 5, "str"))

if __name__ == "__main__":
    unittest.main()


class Depcopy(unittest.TestCase):
    def test_depcopy_origin(self):
        n = run.Norm(R, ["src/a.clj"], "/m", depcopy="/x/depcopy")
        loc = {"uri": "file:///x/depcopy/clj-cold/workspace/.cache/clojure.core.clj", "range": {"start": {"line": 7}}}
        self.assertEqual(n.value("definition", [loc]), "depcopy:clojure.core.clj:7")
        self.assertEqual(n.value("references", [loc, loc]), ["depcopy:clojure.core.clj:7"])

    def test_copy_naming(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = dep.write_copy(Path(d), "jar:file:///m/clojure-1.12.6.jar!/clojure/core.clj", "x")
            self.assertEqual(p.name, "clojure.core.clj")
            self.assertEqual((p.parent / ".clojure.core.metadata").read_text(), "jar:file:///m/clojure-1.12.6.jar!/clojure/core.clj")
