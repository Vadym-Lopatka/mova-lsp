#!/usr/bin/env python3
"""Server-level regression tests (B3 watched files, B8 unknown method, B11 final publish): python3 nx/test/eng2_e2e.py
Needs MOVA_BIN and XDG_CACHE_HOME (copy of ~/.cache/clojure-lsp inside) in the env."""
import os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lspc import C, uri

fails = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " " + str(info)))
    if not ok:
        fails.append(name)


def project():
    root = os.path.realpath(tempfile.mkdtemp(prefix="nxe2"))
    os.makedirs(root + "/src")
    open(root + "/deps.edn", "w").write('{:paths ["src"]}')
    open(root + "/src/a.clj", "w").write("(ns a)\n(defn foo [] 1)\n")
    open(root + "/src/b.clj", "w").write("(ns b (:require [a]))\n(a/foo)\n")
    return root


def refs(c, f, line=1, ch=7):
    r = c.req("textDocument/references", {"textDocument": {"uri": uri(f)}, "position": {"line": line, "character": ch}, "context": {"includeDeclaration": False}})
    return sorted(x["uri"].split("/")[-1] + ":%d" % x["range"]["start"]["line"] for x in (r or {}).get("result") or [])


def open_doc(c, f, text, v=1):
    c.notify("textDocument/didOpen", {"textDocument": {"uri": uri(f), "languageId": "clojure", "version": v, "text": text}})


def t_b8(root):
    c = C()
    c.init(root)
    c.quiet(1, 30)
    for m in ("textDocument/nonexistent", "foo/bar", "$/setTrace"):
        r = c.req(m, {})
        e = (r or {}).get("error") or {}
        check("B8 " + m, e.get("code") == -32601 and e.get("message") == "Method not found" and e.get("data") == {"method": m}, r)
    c.close()


def t_b3(root):
    fa, fb, fc = root + "/src/a.clj", root + "/src/b.clj", root + "/src/c.clj"
    c = C()
    c.init(root, {"didChangeWatchedFiles": {"dynamicRegistration": True}})
    c.quiet(1, 60)
    regs = [r for r in c.reqs if r.get("method") == "client/registerCapability"]
    check("B3 registers the file watchers", len(regs) == 1 and regs[0]["params"]["registrations"][0]["method"] == "workspace/didChangeWatchedFiles"
          and regs[0]["params"]["registrations"][0]["registerOptions"]["watchers"][0]["globPattern"] == "**/*.{clj,cljs,cljc,cljd,edn,bb,clj_kondo,mova}", c.reqs)
    open_doc(c, fa, open(fa).read())
    c.quiet(0.5, 10)
    check("B3 initial refs", refs(c, fa) == ["b.clj:1"], refs(c, fa))
    # create on disk
    open(fc, "w").write("(ns c (:require [a]))\n(a/foo)\n")
    c.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": uri(fc), "type": 1}]})
    time.sleep(1)
    check("B3 create", refs(c, fa) == ["b.clj:1", "c.clj:1"], refs(c, fa))
    check("B3 create publishes diagnostics", c.wait_diag(uri(fc), lambda d: d is not None), c.diags.get(uri(fc)))
    # change on disk
    open(fb, "w").write("(ns b (:require [a]))\n(a/foo)\n(a/foo)\n")
    c.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": uri(fb), "type": 2}]})
    time.sleep(1)
    check("B3 change", refs(c, fa) == ["b.clj:1", "b.clj:2", "c.clj:1"], refs(c, fa))
    # a file open in the editor is skipped
    open_doc(c, fb, "(ns b)\n", 1)
    c.quiet(0.5, 10)
    open(fb, "w").write("(ns b (:require [a]))\n(a/foo)\n(a/foo)\n(a/foo)\n")
    c.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": uri(fb), "type": 2}]})
    time.sleep(1)
    check("B3 open doc skipped", refs(c, fa) == ["c.clj:1"], refs(c, fa))
    # dependents re-finished: delete the definition file's var on disk while a.clj is closed
    os.remove(fc)
    c.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": uri(fc), "type": 3}]})
    time.sleep(1)
    check("B3 delete", refs(c, fa) == [], refs(c, fa))
    check("B3 delete publishes empty diagnostics for the file", c.diags.get(uri(fc)) == [], c.diags.get(uri(fc)))
    check("B3 delete republishes the dependency (now unused var)", c.wait_diag(uri(fa), lambda d: bool(d) and any(x["code"] == "clojure-lsp/unused-public-var" for x in d), 5), c.diags.get(uri(fa)))
    c.close()


def t_b11(root):
    """Fast typing (no pacing, several docs): whatever is coalesced, the FINAL state of every doc must be published."""
    import random
    random.seed(3)
    files = [root + "/src/a.clj", root + "/src/b.clj"]
    for k in range(2):
        files.append(root + "/src/t%d.clj" % k)
        open(files[-1], "w").write("(ns t%d)\n(defn f [] 1)\n" % k)
    c = C()
    c.init(root)
    c.quiet(1, 60)
    us = [uri(f) for f in files]
    base = [open(f).read() for f in files]
    for u, f, t in zip(us, files, base):
        c.notify("textDocument/didOpen", {"textDocument": {"uri": u, "languageId": "clojure", "version": 1, "text": t}})
    c.quiet(0.5, 20)
    ver, bad = [1] * len(us), 0
    for rnd in range(25):
        want = {}
        for _ in range(random.randint(5, 80)):
            i = random.randrange(len(us))
            ver[i] += 1
            good = random.random() < 0.5
            want[i] = good
            c.notify("textDocument/didChange", {"textDocument": {"uri": us[i], "version": ver[i]},
                                                "contentChanges": [{"text": base[i] + "\n(def xx (+ 1 2" + (")" if good else "") + ")"}]})
        for i, good in want.items():
            # broken text -> a syntax finding; balanced text -> none
            if not c.wait_diag(us[i], lambda d, g=good: d is not None and any(x["code"] == "syntax" for x in d) != g, 15):
                bad += 1
                print("  missing final publish: round", rnd, files[i].split("/")[-1], "good" if good else "broken", [x["code"] for x in c.diags.get(us[i], [])])
    check("B11 final state always published (25 bursts x 4 docs)", bad == 0, bad)
    c.close()


if __name__ == "__main__":
    which = sys.argv[1:] or ["b8", "b3", "b11"]
    for name, fn in (("b8", t_b8), ("b3", t_b3), ("b11", t_b11)):
        if name in which:
            root = project()
            try:
                fn(root)
            finally:
                shutil.rmtree(root, ignore_errors=True)
    print("FAILED: %s" % fails if fails else "ALL PASS")
    sys.exit(1 if fails else 0)
