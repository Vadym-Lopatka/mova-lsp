#!/usr/bin/env python3
"""nx Mova project e2e over stdio. Fixture built in a temp dir: a small Mova checkout (stdlib + Rust natives),
a project that embeds it (host natives in its own Rust, two module dirs) and a source index (MOVA_SOURCE_INDEX).
 - `.mova` files are analysed; every top-level dir with `.mova` files is a source path
 - dialect: `(catch e ..)`, forward references, `throw` of any value, bare `go-loop`: no false diagnostics
 - natives / host natives / stdlib vars / default aliases / native namespaces resolve; a real unknown name is reported
 - definition: Rust registration line, stdlib source line, forward reference, other module dir
 - hover: `Mova native (Rust): file:line` + registration source; completion offers Mova names
Usage: XDG_CACHE_HOME=<tmp> python3 nx/test/mova_e2e.py   (MOVA_BIN = the Mova build under test)"""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lspc

FILES = {
    "mova/core/core.mova": ';; core\n(defn update-vals\n  "Applies f to every value."\n  [m f]\n  (reduce-kv (fn [a k v] (assoc a k (f v))) {} m))\n',
    "mova/core/async.mova": "(defmacro go-loop [bindings & body]\n  `(loop ~bindings ~@body))\n",
    "mova/src/builtins/sys.rs": 'fn register(i: &mut Interp) {\n    // (time-ms) -> wall clock, milliseconds.\n    reg(i, "time-ms", ArityHint::Exact(0), time_ms);\n    reg(\n        i,\n        "chan",\n        ArityHint::Range(0, 1), chan);\n    reg_fs(i, "read", read);\n}\n',
    "proj/native/host/Cargo.toml": '[package]\nname = "host"\n\n[dependencies]\nmova = { path = "../../../mova" }\n',
    "proj/native/host/src/lib.rs": 'pub fn register(e: &mut Engine) {\n    e.register_fn_with_arity(\n        "pg-now",\n        Arity::Exact(0), now);\n}\n',
    "proj/src/app/util.mova": "(ns app.util)\n\n(defn double [x] (* 2 x))\n",
    "proj/src/app/main.mova": """(ns app.main
  (:require [app.util :as u]
            [clojure.core.async :as a]))

(defn run [m]
  (let [t (time-ms)
        c (a/chan)
        d (async/chan 1)]
    (go-loop [i 0] (when (< i 2) (recur (inc i))))
    (try (pg-now) (catch e (helper e)))
    [t c d (mova.fs/read "x") (update-vals m u/double) (missing 1)]))

(defn helper [e] (throw (str e)))
""",
    "proj/lib-mova/app/extra.mova": "(ns app.extra\n  (:require [app.main :as main]))\n\n(defn go! [] (main/run {}))\n",
}


def index(mova):
    n = lambda ns, name, line: {"ns": ns, "name": name, "file": "src/builtins/sys.rs", "line": line}
    return {"v": 2, "root": mova,
            "namespaces": [{"ns": "clojure.core", "file": "core/core.mova"}, {"ns": "clojure.core", "file": "core/async.mova"}],
            "natives": [n("clojure.core", "time-ms", 3), n("clojure.core", "chan", 4), n("mova.fs", "read", 8), n("Math", "sqrt", 1)],
            "aliases": [{"ns": "clojure.core.async", "name": k, "to_ns": "clojure.core", "to": k} for k in ("chan", "go-loop")],
            "default_aliases": [{"alias": "async", "ns": "clojure.core.async"}]}


def main():
    base = os.path.realpath(tempfile.mkdtemp(prefix="nxmova"))
    for rel, text in FILES.items():
        os.makedirs(os.path.dirname(f"{base}/{rel}"), exist_ok=True)
        open(f"{base}/{rel}", "w").write(text)
    json.dump(index(base + "/mova"), open(base + "/index.json", "w"))
    os.environ["MOVA_SOURCE_INDEX"] = base + "/index.json"
    root, ok = base + "/proj", True
    main_f, extra_f = root + "/src/app/main.mova", root + "/lib-mova/app/extra.mova"
    mu, eu = lspc.uri(main_f), lspc.uri(extra_f)

    def chk(name, cond, info=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
        ok &= bool(cond)

    c = lspc.C(cwd=root)
    c.req("initialize", {"processId": os.getpid(), "rootUri": "file://" + root, "capabilities": {"textDocument": {"hover": {"contentFormat": ["markdown"]}, "publishDiagnostics": {}}}}, 60)
    c.notify("initialized", {})
    c.notify("textDocument/didOpen", {"textDocument": {"uri": mu, "languageId": "clojure", "version": 1, "text": FILES["proj/src/app/main.mova"]}})
    c.wait_diag(mu, lambda d: d, 20)
    c.quiet(0.8, 30)
    msgs = [d["message"] for d in c.diags.get(mu) or []]
    chk("diagnostics: only the unknown name", msgs == ["Unresolved symbol: missing"], msgs)
    chk("other module dir is analysed (lib-mova)", eu in c.diags, sorted(c.diags))

    def at(uri, line, ch, m="definition", extra=None):
        p = {"textDocument": {"uri": uri}, "position": {"line": line, "character": ch}}
        p.update(extra or {})
        return (c.req("textDocument/" + m, p, 20) or {}).get("result")

    def loc(r):
        return None if not r else "%s:%d" % (r["uri"].replace("file://" + base + "/", ""), r["range"]["start"]["line"] + 1)

    for name, line, ch, want in [
        ("native (time-ms)", 5, 12, "mova/src/builtins/sys.rs:3"),
        ("native through a required alias (a/chan)", 6, 14, "mova/src/builtins/sys.rs:4"),
        ("native through the default alias (async/chan)", 7, 18, "mova/src/builtins/sys.rs:4"),
        ("native namespace with no require (mova.fs/read)", 10, 22, "mova/src/builtins/sys.rs:8"),
        ("host native (pg-now)", 9, 12, "proj/native/host/src/lib.rs:2"),
        ("stdlib source (update-vals)", 10, 33, "mova/core/core.mova:2"),
        ("stdlib macro (go-loop)", 8, 6, "mova/core/async.mova:1"),
        ("forward reference (helper)", 9, 30, "proj/src/app/main.mova:13"),
    ]:
        got = loc(at(mu, line, ch))
        chk("definition: " + name, got == want, got)
    chk("definition: across module dirs", loc(at(eu, 3, 16)) == "proj/src/app/main.mova:5", loc(at(eu, 3, 16)))
    h = ((at(mu, 5, 12, "hover") or {}).get("contents") or {}).get("value", "")
    chk("hover: native location", "Mova native (Rust): src/builtins/sys.rs:3" in h, h)
    chk("hover: registration source and its comment", 'reg(i, "time-ms", ArityHint::Exact(0), time_ms);' in h and "wall clock, milliseconds" in h, h)
    h = ((at(mu, 9, 12, "hover") or {}).get("contents") or {}).get("value", "")
    chk("hover: host native", "Mova native (Rust): native/host/src/lib.rs:2" in h, h)
    h = ((at(mu, 10, 33, "hover") or {}).get("contents") or {}).get("value", "")
    chk("hover: stdlib doc and arglists", "Applies f to every value." in h and "[m f]" in h, h)
    refs = at(mu, 4, 7, "references", {"context": {"includeDeclaration": False}}) or []
    chk("references: other module dir", any(r["uri"] == eu for r in refs), refs)
    # completion on a changed document
    text = "(ns app.c)\n(defn f []\n  [(time-) (pg-) (mova.fs/r) (async/c) (go-)])\n"
    c.notify("textDocument/didChange", {"textDocument": {"uri": mu, "version": 2}, "contentChanges": [{"text": text}]})
    c.quiet(0.5, 20)
    for ch, want in [(9, ["time-ms"]), (15, ["pg-now"]), (28, ["mova.fs/read"]), (38, ["async/chan"]), (44, ["go-loop"])]:
        r = at(mu, 2, ch, "completion")
        got = sorted(i["label"] for i in (r if isinstance(r, list) else (r or {}).get("items", [])))
        chk("completion: %s" % want[0], got == want, got)
    c.close()
    shutil.rmtree(base, ignore_errors=True)
    print("ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
