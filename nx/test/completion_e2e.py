#!/usr/bin/env python3
"""Completion alias edits + completionItem/resolve over stdio: python3 nx/test/completion_e2e.py (MOVA_BIN, XDG_CACHE_HOME from env)."""
import json, os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench"))
import lsp_bench as b

def fail(m):
    print("FAIL", m); sys.exit(1)

root = tempfile.mkdtemp(prefix="nxcomp")
os.makedirs(root + "/src")
open(root + "/deps.edn", "w").write('{:paths ["src"]}')
open(root + "/src/a.clj", "w").write('(ns a\n  (:require [clojure.string :as str]))\n(defn foo "the doc" [x] x)\n')
open(root + "/src/b.clj", "w").write("(ns b)\n")
root = os.path.realpath(root)
ub = "file://" + root + "/src/b.clj"
WANT_TEXT = "(ns b \n  (:require\n    [clojure.string :as str]))"

def session(caps, text):
    s = b.Server(b.DEFAULT_SERVER)
    s.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"processId": None, "rootUri": "file://" + root, "capabilities": caps}})
    s.wait_id(0)
    s.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    doc = {"uri": ub, "languageId": "clojure", "version": 1, "text": text}
    s.send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": doc}})
    time.sleep(1.5)
    return s

def req(s, i, method, params):
    s.send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
    return s.wait_id(i)[1]["result"]

pos = lambda line, ch: {"textDocument": {"uri": ub}, "position": {"line": line, "character": ch}}
strs = lambda items: [i for i in items if i["label"] == "str" and i.get("kind") == 10]
WANT = "(ns b \n  (:require\n    [clojure.string :as str]))"

# 1. no resolveSupport: the edit is inline (JVM computes it eagerly)
s = session({}, "(ns b)\n(st")
item = strs(req(s, 1, "textDocument/completion", pos(1, 3)))
if len(item) != 1 or "data" in item[0]:
    fail(f"plain alias item {item}")
e = item[0].get("additionalTextEdits", [None])[0]
if not e or e["range"] != {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 6}} or e["newText"] != WANT:
    fail(f"inline edit {e}")
s.close()

# 2. resolveSupport: edit + documentation deferred, completionItem/resolve fills them in
caps = {"textDocument": {"completion": {"completionItem": {"resolveSupport": {"properties": ["documentation", "additionalTextEdits"]}}}}}
s = session(caps, "(ns b)\n(st")
item = strs(req(s, 1, "textDocument/completion", pos(1, 3)))
if len(item) != 1 or "additionalTextEdits" in item[0]:
    fail(f"lazy alias item {item}")
un = item[0]["data"]["unresolved"]
if un[0][0] != "alias" or un[0][1]["alias-to-add"] != "str" or un[0][1]["ns-to-add"] != "clojure.string":
    fail(f"unresolved {un}")
r = req(s, 2, "completionItem/resolve", item[0])
if "data" in r or r["additionalTextEdits"][0]["newText"] != WANT:
    fail(f"resolved alias {r}")
s.close()

# 3. documentation of a project var
s = session(caps, "(ns b\n  (:require [a :as a]))\n(a/f)")
items = req(s, 1, "textDocument/completion", pos(2, 4))
foo = [i for i in items if i["label"] == "a/foo"]
if len(foo) != 1 or not any(u[0] == "documentation" for u in foo[0]["data"]["unresolved"]):
    fail(f"doc item {[i['label'] for i in items][:10]}")
r = req(s, 2, "completionItem/resolve", foo[0])
if "data" in r or "the doc" not in json.dumps(r.get("documentation")):
    fail(f"resolved doc {r}")
s.close()
shutil.rmtree(root, ignore_errors=True)
print("ok")
