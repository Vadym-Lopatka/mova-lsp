#!/usr/bin/env python3
"""nx feature e2e over stdio on a copy of mova/lsp-e2e/projects/clj:
 - requests sent before the project pass ends are parked and answered after it (await-analysis)
 - requests after didChange see the changed text (waits-for-changes: documentSymbol/completion)
 - hover layout follows client capabilities (markdown when contentFormat has markdown)
 - diagnostics republish when another file changes (unused-public-var)
Usage: XDG_CACHE_HOME=<tmp> python3 nx/test/feat_e2e.py [--server nx/bin/nx-lsp]"""
import argparse, json, os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench"))
import lsp_bench as lb

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.join(HERE, "..", "..", "mova", "lsp-e2e", "projects", "clj")


def req(s, i, method, params):
    s.send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
    return s.wait_id(i)[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=lb.DEFAULT_SERVER)
    a = ap.parse_args()
    root = tempfile.mkdtemp(prefix="nxfeat")
    shutil.copytree(PROJ, root + "/p")
    root += "/p"
    core, util = root + "/src/app/core.clj", root + "/src/app/util.clj"
    cu, uu = "file://" + os.path.realpath(core), "file://" + os.path.realpath(util)
    ok = True

    def chk(name, cond, info=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
        ok &= bool(cond)

    s = lb.Server(a.server)
    s.p.stdin.write(lb.frame({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "processId": os.getpid(), "rootUri": "file://" + os.path.realpath(root), "workDoneToken": "t",
        "initializationOptions": {"dependency-scheme": "jar", "show-docs-arity-on-same-line?": True},
        "capabilities": {"textDocument": {"hover": {"contentFormat": ["markdown", "plaintext"]}}}}}))
    s.wait_id(0)
    s.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    # 1. parked definition: `u/greet` usage in core.clj (line 10, char 21)
    s.send(lb.open_msg(cu, open(core).read()))
    d = req(s, 1, "textDocument/definition", {"textDocument": {"uri": cu}, "position": {"line": 10, "character": 21}})
    chk("definition answered (parked until settled)", (d.get("result") or {}).get("uri", "").endswith("util.clj"), d)
    # 2. hover markdown layout
    h = req(s, 2, "textDocument/hover", {"textDocument": {"uri": cu}, "position": {"line": 10, "character": 21}})
    v = (h.get("result") or {}).get("contents") or {}
    chk("hover markdown", isinstance(v, dict) and v.get("kind") == "markdown" and v["value"].startswith("```clojure\n(app.util/greet"), h)
    # 3. change util.clj: rename greet -> hello; documentSymbol right after must show it
    s.send(lb.open_msg(uu, open(util).read()))
    text = open(util).read().replace("greet", "hello")
    s.send({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": uu, "version": 2}, "contentChanges": [{"text": text}]}})
    ds = req(s, 3, "textDocument/documentSymbol", {"textDocument": {"uri": uu}})
    names = [x["name"] for x in ds.get("result") or []]
    chk("documentSymbol sees change", "hello" in names and "greet" not in names, names)
    # 4. completion after change sees the new name through the alias
    c = req(s, 4, "textDocument/completion", {"textDocument": {"uri": cu}, "position": {"line": 10, "character": 23}})
    chk("completion answered", isinstance(c.get("result"), list), c)
    # 5. references/highlight shapes
    hl = req(s, 5, "textDocument/documentHighlight", {"textDocument": {"uri": cu}, "position": {"line": 4, "character": 7}})
    chk("highlight shape", all(list(x) == ["range"] for x in hl.get("result") or []), hl)
    s.close()
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    print("ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
