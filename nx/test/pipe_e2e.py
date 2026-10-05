#!/usr/bin/env python3
"""End-to-end pipeline test over stdio: python3 nx/test/pipe_e2e.py  (MOVA_BIN, XDG_CACHE_HOME from env)."""
import json, os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench"))
import lsp_bench as b

def fail(m):
    print("FAIL", m); sys.exit(1)

def collect(s, pred, timeout=10):
    """Messages until pred(m) is true (inclusive)."""
    out, end = [], time.time() + timeout
    while time.time() < end:
        try:
            _, m = s.recv(1)
        except Exception:
            continue
        out.append(m)
        if pred(m):
            return out
    fail(f"timeout, got {out[-3:]}")

root = tempfile.mkdtemp(prefix="nxpipe")
os.makedirs(root + "/src")
open(root + "/deps.edn", "w").write('{:paths ["src"]}')
for i in range(30):
    open(f"{root}/src/f{i}.clj", "w").write(f"(ns f{i})\n(def x {i})\n")
open(root + "/src/broken.clj", "w").write("(ns broken)\n)\n")
s = b.Server(b.DEFAULT_SERVER)
s.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"processId": None, "rootUri": "file://" + root, "capabilities": {}, "workDoneToken": "tok"}})
s.wait_id(0)
s.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
u = "file://" + root + "/src/a.clj"
doc = {"uri": u, "languageId": "clojure", "version": 1, "text": "(ns a)\n)\n"}
s.send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": doc}})
pubs = lambda m: m.get("method") == "textDocument/publishDiagnostics"
msgs = collect(s, lambda m: m.get("method") == "$/progress" and m["params"]["value"]["kind"] == "end")
dg = [m for m in msgs if pubs(m)]
want = {"uri": u, "diagnostics": [{"range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 0}}, "tags": [],
        "message": "Unmatched bracket: unexpected )", "code": "syntax", "severity": 1, "source": "clj-kondo"}]}
# startup lint: every project file with findings is published (JVM :lint-project-files-after-startup?), none without
rp = "file://" + os.path.realpath(root)
got = {m["params"]["uri"]: m["params"] for m in dg}
if got.get(u) != want or len(dg) != 32 or set(got) != {u, rp + "/src/broken.clj"} | {f"{rp}/src/f{i}.clj" for i in range(30)}:
    fail(f"open + startup diag: {len(dg)} pubs {sorted(got)[:3]}")
if got[rp + "/src/broken.clj"]["diagnostics"][0]["message"] != "Unmatched bracket: unexpected )":
    fail("startup broken.clj")
if {d["code"] for i in range(30) for d in got[f"{rp}/src/f{i}.clj"]["diagnostics"]} != {"clojure-lsp/unused-public-var"}:
    fail("startup f*.clj")
pr = [m["params"]["value"] for m in msgs if m.get("method") == "$/progress"]
if pr[0] != {"kind": "begin", "title": "clojure-lsp", "percentage": 0} or pr[-1] != {"kind": "end", "message": "Done"}:
    fail(f"progress ends {pr[0]} {pr[-1]}")
pcts = [p["percentage"] for p in pr if "percentage" in p]
if pcts != sorted(pcts) or pcts[-1] != 99 or 98 not in pcts:
    fail(f"percentages {pcts}")
# change fixes it -> empty diagnostics
s.send({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": u, "version": 2}, "contentChanges": [{"text": "(ns a)\n"}]}})
m = collect(s, pubs)[-1]["params"]
if m != {"uri": u, "diagnostics": []}:
    fail(f"fixed {m}")
# stale change (version 1 < 2): nothing published; then a real change proves the stale one was dropped
s.send({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": u, "version": 1}, "contentChanges": [{"text": ")"}]}})
s.send({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": u, "version": 3}, "contentChanges": [{"text": "(ns a)\n]"}]}})
m = collect(s, pubs)[-1]["params"]
if len(m["diagnostics"]) != 1 or m["diagnostics"][0]["message"] != "Unmatched bracket: unexpected ]":
    fail(f"stale/next {m}")
s.close()
shutil.rmtree(root, ignore_errors=True)
print("ok")
