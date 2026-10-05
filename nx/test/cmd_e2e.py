#!/usr/bin/env python3
"""executeCommand + custom clojure/* e2e: nx answers (response + workspace/applyEdit + showDocument + showMessage) vs goldens
recorded from the true JVM clojure-lsp (`cd cli && clojure -M -m clojure-lsp.main`). Pass/fail (exit 1 on any diff).

  python3 nx/test/cmd_e2e.py                       # check nx against nx/test/cmd_golden.json
  python3 nx/test/cmd_e2e.py --only restructure-keys,clojure/serverInfo/raw [--proj clj]
  python3 nx/test/cmd_e2e.py --record [--proj clj,mv] [--max 6]   # (re)capture goldens from the JVM server

Fixture projects: nx/test/cmd-fixtures/{clj,mv,e2e-clj,e2e-bb,e2e-cljs} (copied to a temp dir per run). Questions to the user -> null.
Env: XDG_CACHE_HOME (private cache; JVM needs a copy of ~/.cache/clojure-lsp), MOVA_BIN (nx Mova binary), JVM_CWD (default <repo>/cli)."""
import argparse, gzip, json, os, re, shutil, subprocess, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import nxenv  # private XDG_STATE_HOME / XDG_CONFIG_HOME for every spawned server

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIX = os.path.join(HERE, "cmd-fixtures")
GOLD = os.path.join(HERE, "cmd_golden.json.gz")
PROJECTS = ["clj", "mv", "e2e-clj", "e2e-bb", "e2e-cljs"]
CAPS = {"workspace": {"workspaceEdit": {"documentChanges": True}, "applyEdit": True}, "window": {"showDocument": {"support": True}}}


class C:
    def __init__(self, cmd, root, env, cwd=None):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=cwd)
        self.root = root
        self.kind = "jvm" if cwd else "nx"
        self.id = 0
        self.resp = {}
        self.cv = threading.Condition()
        self.diags = {}
        self.captured = []
        self.dtime = time.time()
        threading.Thread(target=self.rd, daemon=True).start()
        self.req("initialize", {"processId": None, "rootUri": "file://" + root, "capabilities": CAPS,
                                "initializationOptions": {"dependency-scheme": "jar"}}, 180)
        self.note("initialized", {})

    def rd(self):
        f = self.p.stdout
        while True:
            h = f.readline()
            if not h:
                return
            n = 0
            while h.strip():
                if h.lower().startswith(b"content-length"):
                    n = int(h.split(b":")[1])
                h = f.readline()
            m = json.loads(f.read(n))
            if "method" in m and "id" in m:
                if m["method"] in ("workspace/applyEdit", "window/showDocument", "window/showMessageRequest"):
                    self.captured.append((m["method"], m["params"]))
                res = {"applied": True} if m["method"] == "workspace/applyEdit" else ({"success": True} if m["method"] == "window/showDocument" else None)
                self.send({"jsonrpc": "2.0", "id": m["id"], "result": res})
            elif "method" in m:
                if m["method"] == "window/showMessage":
                    self.captured.append((m["method"], m["params"]))
                if m["method"] == "textDocument/publishDiagnostics":
                    self.diags[m["params"]["uri"]] = m["params"]["diagnostics"]
                    self.dtime = time.time()
            else:
                with self.cv:
                    self.resp[m["id"]] = m
                    self.cv.notify_all()

    def send(self, m):
        b = json.dumps(m).encode()
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
        self.p.stdin.flush()

    def note(self, m, p):
        self.send({"jsonrpc": "2.0", "method": m, "params": p})

    def req(self, m, p, t=60):
        self.id += 1
        i = self.id
        self.send({"jsonrpc": "2.0", "id": i, "method": m, "params": p})
        end = time.time() + t
        with self.cv:
            while i not in self.resp:
                if time.time() > end:
                    return None
                self.cv.wait(0.05)
            return self.resp.pop(i)

    def close(self):
        try:
            self.req("shutdown", None, 5)
            self.note("exit", None)
        except Exception:
            pass
        self.p.kill()



PAREDIT = ["forward-slurp", "forward-barf", "backward-slurp", "backward-barf", "raise-sexp", "kill-sexp", "forward", "backward",
           "forward-select", "backward-select"]
DIRECT = PAREDIT + ["restructure-keys", "create-test", "resolve-macro-as", "drag-param-forward", "drag-param-backward", "drag-forward",
                    "drag-backward", "add-missing-libspec", "cursor-info"]
LANG = {"clj": "clojure", "cljs": "clojurescript", "cljc": "clojure", "bb": "clojure"}


def start(kind, root):
    xdg = os.environ.get("XDG_CACHE_HOME") or tempfile.mkdtemp(prefix="nxcmd-xdg")
    env = dict(os.environ, XDG_CACHE_HOME=xdg, MOVA_SHARDS="4")
    if kind == "jvm":
        res = os.path.join(xdg, "clojure-lsp/jdk/result")
        if os.path.exists(res):
            home = os.path.dirname(os.path.dirname(open(res).read().strip().replace("file://", "")))
            if os.path.exists(os.path.join(home, "bin/java")):
                env.update(JAVA_HOME=home, PATH=home + "/bin:" + env["PATH"])
        return C(["clojure", "-M", "-m", "clojure-lsp.main"], root, env, os.environ.get("JVM_CWD") or os.path.join(WT, "cli"))
    return C([os.path.join(WT, "nx/bin/nx-lsp")], root, env)


def src_files(root):
    out = []
    for d, _, fs in os.walk(os.path.join(root, "src")):
        out += [os.path.relpath(os.path.join(d, f), root) for f in fs if f.rsplit(".", 1)[-1] in LANG]
    return sorted(out)


def open_files(c, root, files):
    for f in files:
        c.note("textDocument/didOpen", {"textDocument": {"uri": "file://" + root + "/" + f, "languageId": LANG[f.rsplit(".", 1)[-1]], "version": 1,
                                                          "text": open(os.path.join(root, f)).read()}})


def settle(c, wait=60, files=None):
    t0 = time.time()
    if files is not None:   # nx: every opened file analysed (diagnostics published) and quiet for 0.3 s
        want = {"file://" + c.root + "/" + f for f in files}
        while time.time() - t0 < wait and (not want <= set(c.diags) or time.time() - c.dtime < 1.0):
            time.sleep(0.05)
        return
    time.sleep(2)
    while time.time() - c.dtime < 2.5 and time.time() - t0 < wait:
        time.sleep(0.3)


VOLATILE = ("log-path", "port", "server-version", "clj-kondo-version")


def norm_result(root, method, res):
    """Drop machine-specific values from custom-request results."""
    if method == "clojure/serverInfo/raw" and isinstance(res, dict):
        res = {k: v for k, v in res.items() if k not in VOLATILE}
        res["classpath"] = sorted(x for x in res.get("classpath", []))
        # external counts depend on which jars the JVM analysed lazily (and differ run to run): keys only; specs hold machine paths
        res["analysis-summary"]["external"] = sorted(res["analysis-summary"].get("external", {}))
        for k in ("java-class-usages", "var-usages"):   # analyzer parity details (clojure-lsp drops some usages), not a serverInfo concern
            res["analysis-summary"]["internal"].pop(k, None)
        fs = res["final-settings"]
        fs.pop("project-specs", None)
        fs["source-paths"] = sorted(fs.get("source-paths", []))
        fs["source-aliases"] = sorted(fs.get("source-aliases", []))
    return res


def sort_changes(o):
    """The JVM orders per-file changes by the hash order of the uris (root dependent): compare them sorted."""
    if isinstance(o, dict):
        if isinstance(o.get("documentChanges"), list):
            o["documentChanges"] = sorted(o["documentChanges"], key=lambda d: json.dumps(d.get("textDocument", {}).get("uri", d.get("uri", "")))+json.dumps(d, sort_keys=True))
        for v in o.values():
            sort_changes(v)
    elif isinstance(o, list):
        for v in o:
            sort_changes(v)
    return o


def run_case(c, root, x, fast=False):
    c.captured = []
    ar = lambda a: ("file://" + root + "/" + a[1:] if isinstance(a, str) and a.startswith("@") else a)
    kind = x["kind"]
    if kind == "cmd":
        r = c.req("workspace/executeCommand", {"command": x["command"], "arguments": [ar(a) for a in x["arguments"]]}, 60)
        res = (r or {}).get("result", (r or {}).get("error"))
    elif kind == "req":
        p = json.loads(re.sub(r'"@([^"]*)"', lambda m: json.dumps("file://" + root + "/" + m.group(1)), json.dumps(x["params"]))) if x["params"] is not None else None
        r = c.req(x["method"], p, 4 if c.kind == "jvm" else 20)
        res = norm_result(root, x["method"], (r or {}).get("result", (r or {}).get("error")))
    else:  # notification: the answer is a window/showMessage
        c.note(x["method"], x["params"] and {k: ({**v, "uri": ar(v["uri"])} if k == "textDocument" else v) for k, v in x["params"].items()})
        end = time.time() + 6
        while not c.captured and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.01 if fast else 0.1)
        res = None
    time.sleep(0.003 if fast else 0.02)
    cap = [(m, ({**p, "message": "<msg>"} if m == "window/showMessage" and (x["kind"] == "note" or x.get("command") in ("server-info", "cursor-info")) else p)) for m, p in c.captured]
    out = json.dumps({"r": res, "c": cap}, sort_keys=True).replace(root, "<root>").replace(os.path.expanduser("~"), "<HOME>")  # jar paths under ~/.m2 differ per machine
    out = re.sub(r"file://[^\"]*?/jdk/java\.base/", "file://<jdk>/java.base/", out)   # where the JDK sources were extracted
    if x.get("command") == "move-form":
        out = json.dumps(sort_changes(json.loads(out)), sort_keys=True)
    return re.sub(r'("data": \{"id": )\d+', r"\g<1>0", out)  # -32603 data carries the request id


def positions(text, n):
    lines = text.split("\n")
    out = []
    for i, l in enumerate(lines):
        for j in range(len(l) + 1):
            if j == 0 or j == len(l) or not l[j - 1].isalnum() or not l[j].isalnum():
                out.append((i, j))
    step = max(1, len(out) // n)
    return out[::step]


def targets(text):
    """Positions of interest: defn names, params, :keys maps, forms start, macro calls."""
    out = []
    for i, l in enumerate(text.split("\n")):
        m = re.match(r"\((defn-?|defmacro|def|defrecord|defprotocol) (\S+)", l)
        if m:
            out += [(i, 0), (i, m.start(2)), (i, m.end(2) + 1)]
        for m in re.finditer(r"\{:keys", l):
            out.append((i, m.start()))
        if l.startswith("(my-mac") or l.startswith("(pa "):
            out += [(i, 1), (i, 0)]
    return out


def gen_cases(c, root, proj, files, per_file):
    """Cases for one project: codeAction-derived commands + direct commands + custom requests."""
    cases = []
    add = lambda **k: cases.append(dict(proj=proj, **k))
    for f in files:
        uri = "file://" + root + "/" + f
        text = open(os.path.join(root, f)).read()
        dg = c.diags.get(uri, [])
        for (l, ch) in sorted(set(positions(text, per_file) + targets(text))):
            ctx = [d for d in dg if (d["range"]["start"]["line"], d["range"]["start"]["character"]) <= (l, ch) <= (d["range"]["end"]["line"], d["range"]["end"]["character"])]
            r = c.req("textDocument/codeAction", {"textDocument": {"uri": uri}, "range": {"start": {"line": l, "character": ch}, "end": {"line": l, "character": ch}}, "context": {"diagnostics": ctx}})
            seen = set()
            for a in (r or {}).get("result") or []:
                cmd = (a.get("command") or {}).get("command")
                if not cmd:
                    continue
                args = [("@" + x[len("file://" + root + "/"):] if isinstance(x, str) and x.startswith("file://" + root + "/") else x) for x in a["command"]["arguments"]]
                seen.add(cmd)
                add(kind="cmd", file=f, line=l, ch=ch, command=cmd, arguments=args)
            for cmd in DIRECT:
                if cmd not in seen:
                    add(kind="cmd", file=f, line=l, ch=ch, command=cmd, arguments=["@" + f, l, ch])
        # move-form: every top-level def start -> every other file of the same language
        for i, line in enumerate(text.split("\n")):
            if line.startswith("(def"):
                for g in files:
                    if g != f and g.rsplit(".", 1)[-1] == f.rsplit(".", 1)[-1]:
                        add(kind="cmd", file=f, line=i, ch=0, command="move-form", arguments=["@" + f, i, 0, g])
                        add(kind="cmd", file=f, line=i, ch=0, command="move-form", arguments=["@" + f, i, 0, "/ROOT/" + g])
    f0 = files[0]
    add(kind="req", method="clojure/serverInfo/raw", params=None)
    add(kind="cmd", file=f0, line=0, ch=0, command="server-info", arguments=[])  # JVM: NPE -> -32603
    add(kind="cmd", file=f0, line=0, ch=0, command="server-info", arguments=["@" + f0, 0, 0])
    add(kind="note", method="clojure/serverInfo/log", params=None)
    for f in files:
        tl = open(os.path.join(root, f)).read().split("\n")
        # JVM never answers cursorInfo/raw on nodes with children (Jackson cannot encode their seq fn): token/whitespace positions only
        tok = [(l, ch) for (l, ch) in positions("\n".join(tl), 4 * per_file) if ch < len(tl[l]) and (tl[l][ch].isalnum() or tl[l][ch] in ":/*-")]
        for (l, ch) in tok[:max(2, per_file // 2)]:
            p = {"textDocument": {"uri": "@" + f}, "position": {"line": l, "character": ch}}
            add(kind="req", method="clojure/cursorInfo/raw", params=json.loads(json.dumps(p)))
    add(kind="note", method="clojure/cursorInfo/log", params={"textDocument": {"uri": "@" + f0}, "position": {"line": 0, "character": 1}})
    add(kind="note", method="clojure/cursorInfo/log", params={"textDocument": {"uri": "@" + f0}, "position": {"line": 2, "character": 1}})
    for sym, ns in [("map", "clojure.core"), ("str", "clojure.core"), ("join", "clojure.string"), ("nope", "nope.ns"), ("when", "cljs.core")]:
        add(kind="req", method="clojure/clojuredocs/raw", params={"sym-name": sym, "sym-ns": ns})
    add(kind="req", method="clojure/workspace/projectTree/nodes", params=None)
    return cases


def tree_cases(c, root, proj):
    """Walk the projectTree on the JVM (children follow from earlier answers); returns requests to record."""
    out, todo = [], [None]
    while todo and len(out) < 40:
        p = todo.pop(0)
        real = json.loads(json.dumps(p).replace("@ROOT", root)) if p else p
        r = c.req("clojure/workspace/projectTree/nodes", real)
        res = (r or {}).get("result")
        out.append(dict(proj=proj, kind="req", method="clojure/workspace/projectTree/nodes", params=p))
        for n in (res or {}).get("nodes", []) if isinstance(res, dict) else []:
            # library/jar levels depend on which jars the JVM analysed lazily: not recorded
            if not n.get("final", True) and n.get("type") in (2, 5):
                todo.append(json.loads(json.dumps(n).replace(root, "@ROOT")))
    return out


def with_server(kind, proj, fn):
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="nxcmd"))   # no symlinked tmp dir (JVM would see /var and /private/var)
    root = os.path.join(tmp, "p")
    shutil.copytree(os.path.join(FIX, proj), root)
    c = start(kind, root)
    try:
        files = src_files(root)
        open_files(c, root, files)
        settle(c, files=files if kind == "nx" else None)
        return fn(c, root, files)
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)


def fix_root(x, root):
    x = json.loads(json.dumps(x).replace("/ROOT/", root + "/").replace("@ROOT", root))
    return x


def record(projs, per_file, only):
    allc = []
    for proj in projs:
        def go(c, root, files):
            cases = gen_cases(c, root, proj, files, per_file)
            cases = [x for x in cases if not (x["kind"] == "req" and x["method"].endswith("projectTree/nodes"))] + tree_cases(c, root, proj)
            print(proj, "cases", len(cases), flush=True)
            for x in cases:
                if only and (x.get("command") or x.get("method")) not in only:
                    continue
                t0 = time.time()
                x["expected"] = run_case(c, root, fix_root(x, root))
                if os.environ.get("VERBOSE") or time.time() - t0 > 5:
                    print(f"  {time.time() - t0:.1f}s {key(x)} {x.get('file')} {x.get('line')}:{x.get('ch')}", flush=True)
                allc.append(x)
        with_server("jvm", proj, go)
        print(proj, len(allc), "cases so far", flush=True)
    if only and os.path.exists(GOLD):
        old = [x for x in load_gold() if (x.get("command") or x.get("method")) not in only or x["proj"] not in projs]
        allc = old + allc
    with gzip.open(GOLD, "wt") as fh:
        json.dump(allc, fh)
    print(f"recorded {len(allc)} cases -> {GOLD}")


def load_gold():
    with gzip.open(GOLD, "rt") as fh:
        return json.load(fh)


def key(x):
    return x.get("command") or x.get("method")


def known_diff(x, got):
    """Documented JVM quirks that nx does not copy (counted, not failed)."""
    k, exp = key(x), x["expected"]
    if k not in ("cursor-info", "server-info") and '"code": -32603' in exp:
        return True   # the JVM throws (internal error) at some positions inside meta / syntax-quote / reader-conditional wrappers
    if k == "add-missing-libspec" and "nsx.a1 :refer [f]" in exp:
        return True   # `f` is defined in two namespaces; the JVM picks by hash order
    if k == "add-missing-libspec" and "app.extra2 :refer [f]" in exp:
        return True   # same ambiguity (`f` in two namespaces), plus clean-ns of a refer to a var shadowed by the file's own def
    if k == "add-missing-libspec" and "cheshire.generate :refer [to-json]" in exp:
        return True   # `to-json` is defined in two namespaces; the JVM picks by hash order
    if k == "inline-symbol" and re.search(r"clojure-1\.12\.\d+\.jar", exp):
        return True   # inlining a clojure.core macro rewrites the jar; usage order differs
    return False


def quick_subset(cases, per_key):
    """Deterministic sample: `per_key` cases for each (project, command/method), spread over the list."""
    groups = {}
    for x in cases:
        groups.setdefault((x["proj"], key(x)), []).append(x)
    out = []
    for g in groups.values():
        step = max(1, len(g) // per_key)
        out += g[::step][:per_key]
    return out


def test(only, projs, quick=0):
    from concurrent.futures import ThreadPoolExecutor
    cases = load_gold()
    if only:
        cases = [x for x in cases if key(x) in only]
    if projs:
        cases = [x for x in cases if x["proj"] in projs]
    if quick:
        cases = quick_subset(cases, quick)
    lock = threading.Lock()
    st = {"bad": 0, "known": 0}
    per = {}

    def one(proj):
        mine = [x for x in cases if x["proj"] == proj]

        def go(c, root, files):
            for x in mine:
                got = run_case(c, root, fix_root(x, root), fast=True)
                with lock:
                    pk = per.setdefault(key(x), [0, 0])
                    pk[0] += 1
                    if got == x["expected"]:
                        continue
                    if known_diff(x, got):
                        pk[0] -= 1
                        st["known"] += 1
                        continue
                    pk[1] += 1
                    st["bad"] += 1
                    if st["bad"] <= int(os.environ.get("SHOW", "6")):
                        w = int(os.environ.get("W", "400"))
                        print(f"FAIL {proj} {key(x)} {x.get('file')}:{x.get('line')}:{x.get('ch')} {json.dumps(x.get('arguments') or x.get('params'))[:120]}\n  want {x['expected'][:w]}\n  got  {got[:w]}")
                        if os.environ.get("DIFF"):
                            i = next((k for k, (a, b) in enumerate(zip(x["expected"], got)) if a != b), min(len(got), len(x["expected"])))
                            print(f"  first diff @{i}\n    want ...{x['expected'][max(0, i - 80):i + 160]}\n    got  ...{got[max(0, i - 80):i + 160]}")
        if mine:
            with_server("nx", proj, go)

    with ThreadPoolExecutor(max_workers=len(PROJECTS)) as ex:
        list(ex.map(one, sorted({x["proj"] for x in cases})))
    for k, (n, b) in sorted(per.items()):
        if b or os.environ.get("VERBOSE"):
            print(f"{'ok  ' if not b else 'FAIL'} {k}: {n - b}/{n}")
    n = sum(v[0] for v in per.values())
    print(f"cmd_e2e: {n - st['bad']}/{n} equal, {st['bad']} differ, {st['known']} known JVM quirks skipped")
    return 1 if st["bad"] else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--proj")
    ap.add_argument("--max", type=int, default=6)
    ap.add_argument("--only")
    ap.add_argument("--quick", type=int, default=0, help="check only N cases per (project, command) -- the gate table")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    projs = a.proj.split(",") if a.proj else None
    if a.record:
        record(projs or PROJECTS, a.max, only)
    else:
        sys.exit(test(only, projs, a.quick))
