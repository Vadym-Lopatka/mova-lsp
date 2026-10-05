#!/usr/bin/env python3
"""lsp-e2e runner (stdlib only). Contract: DESIGN.md."""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, threading, time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse
import dep
import ext as ext_mod

HERE = Path(__file__).resolve().parent
WT = HERE.parent.parent  # worktree root
DEFAULT_MOVA = "mova"  # on PATH; MOVA_BIN overrides
BG_DONE = ":internal/initialize-project-analysis"
DELIMS = set(" \t\r\n,()[]{}'`~@^#")


def xdg_dir():
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "lsp-e2e"


# ---------- LSP client ----------
class Client:
    def __init__(self, cmd, env, cwd, errlog):
        self.p = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=errlog)
        self.t0 = time.monotonic()
        self.wlock, self.nid, self.pending = threading.Lock(), 0, {}
        self.diags, self.diag_ev, self.events = {}, {}, []
        self.diag_at = 0.0
        self.warns = []  # full text of window/logMessage type 2 (warning)
        self.done = threading.Event()
        self.done_at = None
        threading.Thread(target=self._read, daemon=True).start()

    def now(self):
        return int((time.monotonic() - self.t0) * 1000)

    def _frame(self, obj):
        b = json.dumps(obj).encode()
        return b"Content-Length: %d\r\n\r\n" % len(b) + b

    def _write(self, data):
        with self.wlock:
            try:
                self.p.stdin.write(data)
                self.p.stdin.flush()
            except OSError:
                pass

    def notify(self, method, params):
        self._write(self._frame({"jsonrpc": "2.0", "method": method, "params": params}))

    def send_batch(self, reqs):
        """Pipeline: register all futures, write all frames in one go."""
        futs, buf = [], b""
        for method, params in reqs:
            self.nid += 1
            f = Future()
            self.pending[self.nid] = f
            futs.append(f)
            buf += self._frame({"jsonrpc": "2.0", "id": self.nid, "method": method, "params": params})
        self._write(buf)
        return futs

    def request(self, method, params, timeout=20):
        return self.send_batch([(method, params)])[0].result(timeout)

    def _read(self):
        out = self.p.stdout
        while True:
            n = None
            while True:
                line = out.readline()
                if not line:
                    return self._eof()
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":")[1])
            if n is None:
                continue
            self._dispatch(json.loads(out.read(n)))

    def _eof(self):
        for f in list(self.pending.values()):
            if not f.done():
                f.set_exception(RuntimeError("server exited"))

    def _dispatch(self, msg):
        m = msg.get("method")
        if m is None:
            f = self.pending.pop(msg.get("id"), None)
            if f:
                f.set_result({"__error__": msg["error"].get("message")} if "error" in msg else msg.get("result"))
        elif "id" in msg:
            res = [None] * len(msg.get("params", {}).get("items", [])) if m == "workspace/configuration" else None
            self._write(self._frame({"jsonrpc": "2.0", "id": msg["id"], "result": res}))
        else:
            self._notification(m, msg.get("params") or {})

    def _notification(self, m, p):
        if m == "textDocument/publishDiagnostics":
            self.diags[p["uri"]] = p["diagnostics"]
            self.diag_at = time.monotonic()
            self.diag_ev.setdefault(p["uri"], threading.Event()).set()
        elif m == "$/progress" and (p.get("value") or {}).get("kind") == "end" and p.get("token") == "e2e-init":
            self.events.append((self.now(), "progress-end", p.get("token")))
            self._mark_done()
        elif m == "window/logMessage" and BG_DONE in str(p.get("message")) and "finish" in str(p.get("message")).lower():
            self._mark_done()
        if m == "window/logMessage" and p.get("type") == 2:
            self.warns.append(str(p.get("message")))
        if m in ("$/progress", "window/logMessage"):
            self.events.append((self.now(), m, json.dumps(p)[:160]))

    def _mark_done(self):
        if not self.done.is_set():
            self.done_at = self.now()
            self.done.set()

    def close(self):
        try:
            self.request("shutdown", None, 5)
            self.notify("exit", None)
            self.p.wait(3)
        except Exception:
            self.p.kill()


# ---------- probes ----------
def tokenize(text):
    """Yield (offset, token) for symbols/keywords outside strings, comments, char literals."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == ";":
            while i < n and text[i] != "\n":
                i += 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif c == "\\":
            i += 2
            while i < n and text[i] not in DELIMS and text[i] not in '";':
                i += 1
        elif c in DELIMS:
            i += 1
        else:
            j = i
            while j < n and text[j] not in DELIMS and text[j] not in '";':
                j += 1
            tok = text[i:j]
            if not re.match(r"^[-+]?\d", tok) and tok not in ("nil", "true", "false", "&", ".", "::"):
                yield i, tok
            i = j


def build_probes(root, files, n_comp):
    probes = []
    for rel in files:
        text = (root / rel).read_text()
        starts = [0] + [m.end() for m in re.finditer("\n", text)]
        seen, ncomp = set(), 0
        for off, tok in tokenize(text):
            if tok in seen:
                continue
            seen.add(tok)
            line = max(k for k, s in enumerate(starts) if s <= off)
            pos = {"line": line, "character": off - starts[line]}
            td = {"uri": (root / rel).as_uri()}
            key = f"{rel}:{pos['line']}:{pos['character']}"
            base = {"textDocument": td, "position": pos}
            probes.append((f"{key}:definition", "textDocument/definition", base))
            probes.append((f"{key}:hover", "textDocument/hover", base))
            probes.append((f"{key}:references", "textDocument/references", {**base, "context": {"includeDeclaration": True}}))
            if ncomp < n_comp and len(tok) >= 2 and not tok.startswith(":"):
                ncomp += 1
                p2 = {"line": line, "character": off - starts[line] + 2}
                probes.append((f"{key}:completion", "textDocument/completion", {"textDocument": td, "position": p2}))
    return probes


# ---------- normalization ----------
# owner editor (lsp-mode, lsp-clojure.el) init options
INIT_OPTIONS = {"dependency-scheme": "jar", "show-docs-arity-on-same-line?": True}

class Norm:
    def __init__(self, root, files, mova_root=None, depcopy=None):
        self.depcopy = os.path.realpath(depcopy or xdg_dir() / "depcopy")  # lsp-mode style dependency copies live here
        self.root, self.files = str(root.resolve()), set(files)
        self.mova_root = str(Path(mova_root).resolve()) if mova_root else str(WT / "mova")

    def path(self, uri):
        if uri.startswith(("jar:", "zipfile:")):
            jar, entry = (re.split(r"!/|::", uri, maxsplit=1) + [""])[:2]
            jar = os.path.basename(unquote(urlparse(jar.split(":", 1)[1]).path))
            return ("jdk", entry) if jar == "src.zip" else ("jar", f"{jar}!/{entry}")
        p = os.path.realpath(unquote(urlparse(uri).path))
        if p.startswith(self.depcopy + "/"):
            return "depcopy", os.path.basename(p)  # stable key: <ns>.<ext>
        if p.startswith(self.root + "/"):
            return "project", p[len(self.root) + 1:]
        if "/clojure-lsp/jdk/" in p or "/nx/jdk/" in p:
            return "jdk", p.split("/jdk/", 1)[1]
        if p.startswith(self.mova_root + "/"):
            return "mova", os.path.relpath(p, self.mova_root)
        home = os.path.realpath(os.path.expanduser("~"))
        return "other", ("<HOME>" + p[len(home):]) if p.startswith(home + "/") else p

    def loc(self, x):
        if not isinstance(x, dict):
            return None
        uri = x.get("uri") or x.get("targetUri")
        rng = x.get("range") or x.get("targetSelectionRange") or x.get("targetRange") or {}
        if not uri:
            return None
        o, p = self.path(uri)
        return f"{o}:{p}:{rng.get('start', {}).get('line', 0)}"

    def value(self, method, res):
        if isinstance(res, dict) and "__error__" in res:
            return "ERR:" + str(res["__error__"])
        if method == "definition":
            return self.loc(res[0] if isinstance(res, list) and res else res)
        if method == "references":
            return sorted({self.loc(x) for x in res or []} - {None})
        if method == "hover":
            c = (res or {}).get("contents") if isinstance(res, dict) else None
            if isinstance(c, list):
                c = c[0] if c else None
            text = c.get("value") if isinstance(c, dict) else c
            return next((l.strip() for l in (text or "").splitlines() if l.strip()), None)
        items = res.get("items", []) if isinstance(res, dict) else res or []
        return sorted(i.get("label", "") for i in items)[:30]

    def origin(self, loc):
        if not isinstance(loc, str) or ":" not in loc:
            return None
        o, rest = loc.split(":", 1)
        if o == "mova" and not rest.rsplit(":", 1)[0].endswith(".mova"):
            return "mova-native"  # Rust natives are never late_ok
        if o == "project" and rest.rsplit(":", 1)[0] not in self.files:
            return "project-other"
        return o

    def diags(self, ds):
        return sorted(f"{d['range']['start']['line']}:{d['range']['start']['character']}:{d.get('code')}" for d in ds)


# ---------- one server run ----------
def wait_all(futs, timeout=20):
    out = []
    for f in futs:
        try:
            out.append(f.result(timeout))
        except Exception as e:
            out.append({"__error__": str(e)})
    return out


AWAITED = ("definition", "hover", "references")  # methods that wait for the bg pass when empty


def run_batch(c, probes, norm, raw=None, timeout=20):
    """raw (list, optional): receives (key, method, raw-result) for every probe."""
    res = wait_all(c.send_batch([(m, p) for _, m, p in probes]), timeout)
    if raw is not None:
        raw.extend((k, m, r) for (k, m, _), r in zip(probes, res))
    return {k: norm.value(m.split("/")[1], r) for (k, m, _), r in zip(probes, res)}


def run_await(c, probes, norm, files, root, cap):
    """SETTLED server, bg pass running: probe batch at once, then a cheap request that must be answered before bg-done."""
    futs = c.send_batch([(m, p) for _, m, p in probes])
    t = time.monotonic()
    ds = c.send_batch([("textDocument/documentSymbol", {"textDocument": {"uri": (root / files[0]).as_uri()}})])[0]
    try:
        ds_res = ds.result(5)
        ds_ok = not (isinstance(ds_res, dict) and "__error__" in ds_res)
    except Exception:
        ds_ok = False
    ds_ms = int((time.monotonic() - t) * 1000)
    ds_before_done = not c.done.is_set() or any(not f.done() for f in futs)  # answered while parked requests / bg pass still pending
    dl = time.monotonic() + cap  # one shared deadline for the whole batch
    res = []
    for f in futs:
        res.extend(wait_all([f], max(0.01, dl - time.monotonic())))
    time.sleep(0.05)
    got = {k: norm.value(m.split("/")[1], r) for (k, m, _), r in zip(probes, res) if m.split("/")[1] in AWAITED}
    return got, dict(ds_ok=ds_ok and ds_before_done, ds_ms=ds_ms, answered_at=c.now(),
                     slow=any(r == {"__error__": ""} for r in res) or any("gave up after" in x for x in c.warns))


def jar_targets(raw, tok_of, limit=3):
    """Distinct jar/zipfile definition results (raw uri kept): [(uri, line, col, name)], first `limit`."""
    seen, out = set(), []
    for k, m, r in raw:
        x = r[0] if isinstance(r, list) and r else r
        uri = (x.get("uri") or x.get("targetUri")) if isinstance(x, dict) else None
        if m != "textDocument/definition" or not uri or not uri.startswith(("jar:", "zipfile:")):
            continue
        st = (x.get("range") or x.get("targetSelectionRange") or {}).get("start", {})
        t = (uri, st.get("line", 0), st.get("character", 0))
        if t not in seen and len(out) < limit:
            seen.add(t)
            out.append((*t, tok_of(k).split("/")[-1]))
    return out


def check_dep_text(text, line, name):
    """None if the jar entry text is a non-empty string holding `name` at 0-based `line`; else a detail."""
    if not isinstance(text, str) or not text:
        return f"got={json.dumps(text)[:60]}"
    ls = text.split("\n")
    if line >= len(ls) or name not in ls[line]:
        return f"line {line} lacks {name!r}: {(ls[line] if line < len(ls) else '<past end>')[:60]!r}"
    return None


def _err(r):
    return f"error={r['__error__'][:80]}" if isinstance(r, dict) and "__error__" in r else None


def run_jar(c, targets):
    """After the settled batch: dependencyContents + hover inside the jar uri (as lsp-mode does). [(check, uri, detail|None)]."""
    res = []
    for uri, line, col, name in targets:
        txt = wait_all([c.send_batch([("clojure/dependencyContents", {"uri": uri})])[0]], 10)[0]
        res.append(("dependencyContents", uri, _err(txt) or check_dep_text(txt, line, name)))
        h = wait_all([c.send_batch([("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": line, "character": col}})])[0]], 10)[0]
        res.append(("hover", uri, _err(h)))
    return res


def probe_tokens(root, files):
    """{'<rel>:<line>:<col>': token} for every tokenized symbol (the probe keys)."""
    toks = {}
    for rel in files:
        text = (root / rel).read_text()
        st = [0] + [m.end() for m in re.finditer("\n", text)]
        for off, tok in tokenize(text):
            ln = max(k for k, s0 in enumerate(st) if s0 <= off)
            toks.setdefault(f"{rel}:{ln}:{off - st[ln]}", tok)
    return toks


def run_server(cmd, env, cwd, root, manifest, phases, cap, errlog, dbase=None, settled_only=False, ext=False):
    """phases: subset of ('early','settled'). Returns dict with results and timings."""
    files, norm = manifest["files"], Norm(root, manifest["files"], index_root(env))
    probes = build_probes(root, files, manifest.get("completion_prefixes", 20))
    c = Client(cmd, env, cwd, errlog)
    out = {"ms": {}, "probes": len(probes)}
    t = c.now()
    init = c.request("initialize", {"processId": os.getpid(), "rootUri": root.as_uri(),
                                    "capabilities": {"window": {"workDoneProgress": True}},
                                    "initializationOptions": INIT_OPTIONS, "workDoneToken": "e2e-init"}, 30)
    c.notify("initialized", {})
    if "projdiag" in phases:  # ext: no didOpen at all; diagnostics the server publishes for the whole project after startup
        c.done.wait(cap)
        out["bg_signal"] = c.done.is_set()
        for _ in range(40):
            if time.monotonic() - c.diag_at > 0.3:
                break
            time.sleep(0.05)
        out["ext_proj"] = ext_mod.project_diagnostics(c, root, norm)
        c.close()
        return out
    if "restart" in phases:  # restart flow: the restored lsp-mode copy is the FIRST didOpen, before any project file
        cps = sorted(p for p in (dbase / "main" / "workspace" / ".cache").glob("*.clj*") if not p.name.startswith(".")) if dbase else []
        if cps:
            dd, dms = dep.open_doc(c, cps[0].as_uri(), cps[0].read_text(), "clojurescript" if cps[0].suffix == ".cljs" else "clojure", 10)
            out["dep_early"], out["dep_early_ms"] = {"depcopy:open:diagnostics": norm.diags(dd)}, dms
        c.p.kill()
        return out
    for rel in files:
        c.diag_ev.setdefault((root / rel).as_uri(), threading.Event())
        c.notify("textDocument/didOpen", {"textDocument": {"uri": (root / rel).as_uri(), "languageId": "clojure",
                                                            "version": 1, "text": (root / rel).read_text()}})
    dl = time.monotonic() + 3  # settled-only (servers that may never publish for clean files): one shared 3 s deadline
    for rel in files:
        c.diag_ev[(root / rel).as_uri()].wait(max(0.05, dl - time.monotonic()) if settled_only else min(cap, 10))
    out["ms"]["open"], t = c.now() - t, c.now()
    if "early" in phases:
        raw = []
        out["early"] = run_batch(c, probes, norm, raw)
        out["early_empty"] = sum(1 for k, m, r in raw if m.split("/")[1] in AWAITED and (r is None or r == [] or r == {}))
        dp = next(p for k, m, p in probes if m == "textDocument/definition")  # cancel one parked request: no warning
        cf = c.send_batch([("textDocument/definition", dp)])[0]
        time.sleep(0.05)
        c.notify("$/cancelRequest", {"id": c.nid})
        cres = wait_all([cf], 5)[0]
        out["early_cancel_ok"] = isinstance(cres, dict) and "cancelled" in str(cres.get("__error__"))
        time.sleep(0.3)  # let the last warning notifications arrive
        out["early_warns"] = list(c.warns)
        out["early_end_at"] = c.now()
        out["ms"]["early"] = c.now() - t
        out["events"] = c.events
        c.p.kill()  # gated bg pass never ran: nothing to shut down
        return out
    if not settled_only:
        out["await"], out["await_info"] = run_await(c, probes, norm, files, root, cap)
        out["ms"]["await"], t = c.now() - t, c.now()
    c.done.wait(cap)
    out["bg_done_at"], out["bg_signal"] = c.done_at, c.done.is_set()
    out["ms"]["wait-bg"], t = c.now() - t, c.now()
    raw = []
    out["settled"] = run_batch(c, probes, norm, raw)
    toks = probe_tokens(root, files)
    jt = jar_targets(raw, lambda k: toks.get(k.rsplit(":", 1)[0], ""), 9)
    out["jar"] = run_jar(c, jt[:3])
    if manifest.get("dep_scenarios") and dbase and not settled_only:
        out["dep"], out["dep_ms"] = dep.run_all(sys.modules[__name__], c, jt, manifest["dep_scenarios"], norm, dbase, cap)
    for _ in range(40):  # post-bg diagnostics republish may lag behind the probes (CPU-starved): wait until quiet 150 ms
        if time.monotonic() - c.diag_at > 0.15:
            break
        time.sleep(0.05)
    for rel in files:
        out["settled"][f"{rel}:diagnostics"] = norm.diags(c.diags.get((root / rel).as_uri(), []))
    if ext:  # extended probes (ext.py): runtime-created messy file is opened too; after the core keys so they are unaffected
        rels = list(files)
        mrel, msrc = ext_mod.messy_rel(root.name, files)
        if mrel:
            (root / mrel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(msrc, root / mrel)
            uri = (root / mrel).as_uri()
            c.diag_ev.setdefault(uri, threading.Event())
            c.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "clojure", "version": 1, "text": (root / mrel).read_text()}})
            c.diag_ev[uri].wait(3)
            time.sleep(0.2)
            rels.append(mrel)
        legend = ((init or {}).get("capabilities", {}).get("semanticTokensProvider") or {}).get("legend")
        out["ext"] = ext_mod.Ext(c, root, norm, rels, legend, wait_all).run(rels)
    out["ms"]["settled"] = c.now() - t
    out["events"] = c.events
    c.close()
    return out


# ---------- comparison ----------
def empty(v):
    return v is None or v == [] or v == ""


def hover_early_checked(prefix, gold, norm):
    """True when the golden definition at this position is jdk or in the same file."""
    d = gold.get(prefix + ":definition")
    if str(gold.get(prefix + ":hover")).startswith("calling:"):  # macro-call hover needs the late jar var
        return False
    if norm.origin(d) == "jdk":
        return True
    return isinstance(d, str) and d.startswith("project:") and d[8:].rsplit(":", 1)[0] == prefix.rsplit(":", 2)[0]


def early_ok(key, got, want, gold, norm, late_ok):
    """EARLY: exact, or empty where the golden answer lives in a late_ok origin."""
    if got == want:
        return True
    prefix, method = key.rsplit(":", 1)
    if method == "definition":
        return got is None and norm.origin(want) in late_ok
    if method == "references" and isinstance(got, list):
        return set(got) <= set(want) and all(norm.origin(x) in late_ok for x in set(want) - set(got))
    if method == "hover":  # compared early only for jdk / same-file definitions, else skipped
        return not hover_early_checked(prefix, gold, norm)
    if method == "completion":  # any non-error answer while the golden definition here is late_ok (partial lists are fine)
        return norm.origin(gold.get(prefix + ":definition")) in late_ok and not str(got).startswith("ERR:")
    return False


def compare(name, cache, phase, got, gold, allow, norm, late_ok, only_gold=False):
    """Returns fail lines (one per failing key). only_gold: compare just the golden keys."""
    fails = []
    for k in sorted(gold if only_gold else set(gold) | set(got)):
        g, w = got.get(k), gold.get(k)
        if phase == "early" and k.endswith(":diagnostics"):
            continue
        if (name, k) in allow or g == w:
            continue
        if phase == "early" and k in gold and early_ok(k, g, w, gold, norm, late_ok):
            continue
        fails.append(f"FAIL {name} {cache} {phase} {k} got={json.dumps(g)} want={json.dumps(w)}")
    return fails


# ---------- orchestration ----------
def index_root(env):
    """Mova repo root from the source index the server uses (None if no index)."""
    try:
        return json.loads(Path(env["MOVA_SOURCE_INDEX"]).read_text())["root"]
    except (KeyError, OSError, ValueError):
        return None


def make_env(args, xdg):
    env = dict(os.environ)
    env.update(MOVA_BIN=args.mova_bin, MOVA_IMAGE=str(xdg / "lsp.img"), CLOJURE_LSP_DEPS_STORE=str(xdg / "store"))
    return env


def build_image(cmd, env):
    subprocess.run(cmd + ["--version"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def merge(parts):
    """Join the early and settled server results of one (project, cache)."""
    out = {}
    for p in parts:
        r = out.setdefault((p["name"], p["cache"]), {"ms": {}})
        r["ms"].update(p["ms"])
        if p["phase"] == "restart":
            r.update({k: v for k, v in p.items() if k.startswith("dep_early")})
            continue
        for k, v in p.items():
            if k != "ms":
                r[k] = v if k not in r or p["phase"] == "settled" or k in ("early", "early_end_at") else r[k]
    res = list(out.values())
    for r in res:  # warm reuses the cold early results of the same project (same probes)
        if "early" not in r:
            src = next((x for x in res if x["name"] == r["name"] and "early" in x), None)
            if src:
                r["early_from"] = src["cache"]
                for k in ("early", "early_end_at", "early_empty", "early_warns", "early_cancel_ok"):
                    r[k] = src.get(k)
    return res


def save_seed(results, seed_dir, xdg, args):
    """Keep the cljs project cache (jar analysis) of a finished cold run as the next seed."""
    for r in results:
        if r["name"] == "cljs" and r["cache"] in ("cold", "cold-seed") and r.get("bg_signal") and \
                (args.full or not seed_dir.exists()) and (r["root"] / ".lsp").exists():
            shutil.rmtree(seed_dir, ignore_errors=True)
            for d in (".lsp", ".clj-kondo"):
                if (r["root"] / d).exists():
                    shutil.copytree(r["root"] / d, seed_dir / d)


def golden_path(name):
    return HERE / ("expected" if name == "mova" else "golden") / f"{name}.json"


def job(args, name, cache, phase, root, cmd, env, errdir):
    """One server: phase 'early' (gate never released, killed after probes) or 'settled' (no gate)."""
    manifest = json.loads((root / "e2e.json").read_text())
    tag = f"{name}-{cache}-{phase}"
    gate = str(errdir / f"{tag}.gate")
    if phase == "early":  # gate never created; short await timeout so the give-up warnings show up
        env = dict(env, CLOJURE_LSP_TEST_BG_GATE=gate, CLOJURE_LSP_AWAIT_ANALYSIS_TIMEOUT_MS="300")
    if (HERE / "expected" / f"{name}.json").exists():
        env["MOVA_SOURCE_INDEX"] = str(HERE / "mova-source-index.sample.json")
    cap = 30 if args.full else manifest.get("bg_cap_s", 8)
    with open(errdir / f"{tag}.log", "wb") as errlog:
        r = run_server(cmd, env, None, root, manifest, (phase,), cap, errlog, xdg_dir() / "depcopy" / f"{name}-{cache}", getattr(args, "settled_only", False), ext=(getattr(args, "ext", False) and phase == "settled"))
    r.update(name=name, cache=cache, phase=phase, manifest=manifest, root=root,
             norm=Norm(root, manifest["files"], index_root(env)))
    return r


def prepare_root(name, cache, phase, xdg, tmps, seed=None):
    """Cold: fresh copy (optional cache seed). Warm: persistent dir; the early server gets its own copy."""
    src = HERE / "projects" / name
    if cache == "warm":  # persistent dir per phase: caches (incl. the did-open sidecar, keyed by path) survive runs
        warm = xdg / (name if phase == "settled" else f"{name}-{phase}")
        shutil.copytree(src, warm, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".lsp", ".clj-kondo"))  # refresh sources
        cpf = xdg / name / ".lsp" / ".cache" / "classpath.edn"  # resolved classpath of the last settled run (restart flow)
        if phase == "restart" and cpf.exists():
            (warm / ".lsp" / ".cache").mkdir(parents=True, exist_ok=True)
            shutil.copy(cpf, warm / ".lsp" / ".cache" / "classpath.edn")
        return warm
    if name == "cljs" and phase == "settled" and cache != "warm":  # fixed path: the cache is valid only for the same project root
        dst = xdg / "cold-cljs" / name
        shutil.rmtree(dst.parent, ignore_errors=True)
    else:
        dst = Path(tempfile.mkdtemp(prefix=f"e2e-{name}-")).resolve() / name
        tmps.append(dst.parent)
    shutil.copytree(src, dst, ignore=None if cache == "warm" else shutil.ignore_patterns(".lsp"))
    if seed and cache != "warm" and phase == "settled":
        shutil.copytree(seed, dst, dirs_exist_ok=True)  # .lsp db + .clj-kondo jar cache
    return dst


def jdk_pinned_env(env):
    """Run the JVM oracle on the JDK whose sources clojure-lsp installed (~/.cache/clojure-lsp/jdk/result),
    not the machine default: JDK member lines differ between versions (golden drift seen: Java 25 vs 21)."""
    res = Path(os.path.expanduser("~/.cache/clojure-lsp/jdk/result"))
    if res.exists():
        src = res.read_text().strip().replace("file://", "")  # .../<jdk-home>/lib/src.zip
        home = Path(src).parent.parent
        if (home / "bin" / "java").exists():
            env = dict(env, JAVA_HOME=str(home), PATH=f"{home / 'bin'}:{env.get('PATH', '')}")
            print(f"golden JVM pinned to {home}")
    return env


def update_golden(args, names, xdg, tmps, errdir):
    env = jdk_pinned_env(dict(os.environ))
    for name in names:
        root = prepare_root(name, "cold", "early", xdg, tmps)
        manifest = json.loads((root / "e2e.json").read_text())
        with open(errdir / f"{name}-jvm.log", "wb") as errlog:
            r = run_server(["clojure", "-M", "-m", "clojure-lsp.main"], env, str(WT / "cli"), root,
                           manifest, ("settled",), 90, errlog, xdg / "depcopy" / f"{name}-jvm")
        golden_path(name).parent.mkdir(exist_ok=True)
        keys = {**r["settled"], **r.get("dep", {})}
        golden_path(name).write_text(json.dumps(keys, indent=1, sort_keys=True) + "\n")
        print(f"golden {name}: {len(keys)} keys ({len(r.get('dep', {}))} depcopy), {r['ms']}")


def update_golden_ext(args, names, xdg, tmps, errdir):
    """Record JVM answers for the extended probes (ext.py) into golden/<name>.ext.json."""
    env = jdk_pinned_env(dict(os.environ))
    for name in names:
        keys = {}
        for phase in ("settled", "projdiag"):
            root = prepare_root(name, "cold", "early", xdg, tmps)
            manifest = json.loads((root / "e2e.json").read_text())
            with open(errdir / f"{name}-jvm-ext-{phase}.log", "wb") as errlog:
                r = run_server(["clojure", "-M", "-m", "clojure-lsp.main"], env, str(WT / "cli"), root, manifest, (phase,), 90, errlog,
                               xdg / "depcopy" / f"{name}-jvm", False, ext=(phase == "settled"))
            keys.update(r.get("ext", {}))
            keys.update(r.get("ext_proj", {}))
        ext_golden_path(name).write_text(json.dumps(keys, indent=1, sort_keys=True) + "\n")
        print(f"ext golden {name}: {len(keys)} keys")


def ext_golden_path(name):
    return HERE / "golden" / f"{name}.ext.json"


def summary_row(name, cache, phase, total, nfail, ms):
    """Table row; pass count is never negative."""
    return (name, cache, phase, total, max(0, total - nfail), nfail, ms)


def await_checks(r, gold, allow, sub):
    """EARLY: one warning per empty awaited answer. AWAIT (in the SETTLED server): exact golden answers, cheap request served before bg-done."""
    n, fails = r["name"], []
    if r.get("early_from"):  # warnings/cancel are checked once per project (cold early server)
        return _await_part(r, gold, allow, n, fails)
    w = r.get("early_warns", [])
    bad = [x for x in w if "gave up after 0.3 s waiting for project analysis" not in x]
    if len(w) != r.get("early_empty") or bad:
        fails.append(f"FAIL {n} {r['cache']} early warnings got={len(w)} want={r.get('early_empty')} bad={bad[:1]}")
    if not r.get("early_cancel_ok"):
        fails.append(f"FAIL {n} {r['cache']} early $/cancelRequest did not cancel the parked request")
    return _await_part(r, gold, allow, n, fails)


def _await_part(r, gold, allow, n, fails):
    info = r.get("await_info", {})
    if not info.get("ds_ok"):
        fails.append(f"FAIL {n} {r['cache']} await documentSymbol not answered before bg-done (blocking)")
    if info.get("slow"):
        fails.append(f"SLOW {n} {r['cache']} await answers not complete within the cap; not compared")
        return fails
    # covered = keys whose EARLY answer was empty (the only ones the await feature handles by design)
    want = {k: v for k, v in gold.items() if k.rsplit(":", 1)[1] in AWAITED and empty(r.get("early", {}).get(k, "x"))}
    r["await_total"] = len(want)
    r["await_partial"] = sum(1 for k, v in gold.items() if k.rsplit(":", 1)[1] in AWAITED and k not in want
                             and r.get("await", {}).get(k) != v)
    for k in sorted(want):
        g = r.get("await", {}).get(k)
        if (n, k) not in allow and g != want[k]:
            fails.append(f"FAIL {n} {r['cache']} await {k} got={json.dumps(g)} want={json.dumps(want[k])}")
    return fails


def report(results, args, wall, load0):
    allow = {(a["project"], a["key"]) for a in json.loads((HERE / "allow.json").read_text())} \
        if (HERE / "allow.json").exists() else set()
    fails, rows = [], []
    for r in results:
        gold = json.loads(golden_path(r["name"]).read_text())
        gdep = {k: v for k, v in gold.items() if k.startswith("depcopy:")}
        gold = {k: v for k, v in gold.items() if k not in gdep}
        if "dep" in r or gdep:  # S1 dependency-file scenarios: exact JVM golden
            fs = compare(r["name"], r["cache"], "dep", r.get("dep", {}), gdep, allow, r["norm"], [])
            fails += fs
            rows.append(summary_row(r["name"], r["cache"], "dep", len(gdep), len(fs), sum(r.get("dep_ms", {}).values())))
            if r.get("dep_ms", {}).get("open_ms", 0) > 150 and args.store_warm:
                fails.append(f"SLOW dep-open {r['name']} {r['cache']} open_ms={r['dep_ms']['open_ms']} > 150 with a warm store")
        if "dep_early" in r and "depcopy:open:diagnostics" in gdep:  # restart flow: JVM-equal; a warm-store hit is fast
            fs = compare(r["name"], r["cache"], "dep-restart", r["dep_early"], {"depcopy:open:diagnostics": gdep["depcopy:open:diagnostics"]}, allow, r["norm"], [])
            fails += fs
            if r["dep_early_ms"] > 150 and args.store_warm:
                fails.append(f"SLOW dep-open-restart {r['name']} {r['cache']} open_ms={r['dep_early_ms']} > 150 with a warm store")
            print(f"dep-open-restart {r['name']} {r['cache']}: open_ms={r['dep_early_ms']}")
        late_ok = r["manifest"].get("late_ok", [])
        sub = golden_path(r["name"]).parent.name == "expected"  # hand-written: compare only its keys
        if not getattr(args, "settled_only", False):
            fails += await_checks(r, gold, allow, sub)
        for phase in (("settled",) if getattr(args, "settled_only", False) else ("early", "settled")):
            if phase == "early" and r.get("early_from"):  # not run here (shared with cold)
                rows.append((r["name"], r["cache"], phase, None, None, None, None))
                continue
            if phase == "settled" and not r["bg_signal"]:  # too slow: answers before bg-done are not evidence
                fails.append(f"SLOW {r['name']} {r['cache']} background pass not done within the cap; settled not compared")
                rows.append(summary_row(r["name"], r["cache"], phase, 0, 0, r["ms"].get(phase, 0)))
                continue
            fs = compare(r["name"], r["cache"], phase, r[phase], gold, allow, r["norm"], late_ok, sub)
            fails += fs
            total = len(gold) if sub else len(set(gold) | set(r[phase]))
            rows.append(summary_row(r["name"], r["cache"], phase, total, len(fs), r["ms"].get(phase, 0)))
    for r in (x for x in results if "await_info" in x and not getattr(args, "settled_only", False)):
        na = r.get("await_total", 0)
        nf = len([f for f in fails if f.startswith(f"FAIL {r['name']} {r['cache']} await")])
        rows.append(summary_row(r["name"], r["cache"], "await", na, nf, r["ms"].get("await", 0)))
    for r in results:
        if "jar" in r:
            jf = [f"FAIL {r['name']} {r['cache']} jar {c} {u} {d}" for c, u, d in r["jar"] if d]
            fails += jf
            rows.append(summary_row(r["name"], r["cache"], "jar", len(r["jar"]), len(jf), r["ms"].get("jar", 0)))
    over = wall > args.budget and not args.full
    if args.json:
        print(json.dumps({"fails": fails, "rows": rows, "wall_s": wall, "over_budget": over}))
    else:
        for f in fails:
            print(f)
        print(f"{'project':8} {'cache':5} {'phase':8} {'probes':>6} {'pass':>5} {'fail':>5} {'ms':>6}")
        print("load at start: %.2f %.2f %.2f" % load0)
        for row in rows:
            print("%-8s %-5s %-8s %6d %5d %5d %6d" % row if row[3] is not None else "%-8s %-5s %-8s %6s %5s %5s %6s" % (*row[:3], *["—"] * 4))
        for r in results:
            print(f"{r['name']} {r['cache']}: ms={r['ms']} early_end={r.get('early_end_at')} bg_done={r.get('bg_done_at')}")
        for r in results:
            print(f"note {r['name']} {r['cache']}: await left {r.get('await_partial', 0)} non-empty early (partial) answers as-is, by design")
        for r in results:
            if r.get("dep_ms"):
                print(f"dep-open {r['name']} {r['cache']}: " + " ".join(f"{k}={v}" for k, v in r["dep_ms"].items()))
        print(f"mode {'full' if args.full else 'default'}")
        print(f"wall {wall:.2f}s budget {args.budget}s {'OVER' if over else 'ok'}")
    return 1 if fails or over else 0


def collect(args, names, xdg, tmps, errdir):
    """Start all servers in parallel, return merged per-(project, cache) results."""
    cmd = args.server_cmd.split() if args.server_cmd else [str(WT / "mova/bin/clojure-lsp")]
    env = make_env(args, xdg)
    args.store_warm = any((xdg / "store").glob("*")) if (xdg / "store").is_dir() else False  # store filled before this run
    build_image(cmd, env)
    seed_dir = xdg / "seed-cljs"
    seed = seed_dir if seed_dir.exists() and not args.full else None
    jobs = []
    for n in names:
        for c in (("cold",) if args.settled_only else ("cold", "warm")):
            label = "cold-seed" if c == "cold" and n == "cljs" and seed else c
            warm_early = json.loads((HERE / "projects" / n / "e2e.json").read_text()).get("warm_early")
            for ph in (("settled",) if (c == "warm" and not warm_early) or args.settled_only else ("early", "settled")):  # warm early: only where the manifest asks (did-open sidecar path)
                root = prepare_root(n, c, ph, xdg, tmps, seed if n == "cljs" else None)
                jobs.append((n, label, ph, root))
            if getattr(args, "ext", False) and c == "cold":  # project-wide diagnostics after a no-open startup
                jobs.append((n, label, "projdiag", prepare_root(n, c, "projdiag", xdg, tmps)))
            if c == "warm" and json.loads((HERE / "projects" / n / "e2e.json").read_text()).get("dep_scenarios"):
                jobs.append((n, label, "restart", prepare_root(n, c, "restart", xdg, tmps)))
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = [ex.submit(job, args, n, c, ph, root, cmd, env, errdir) for n, c, ph, root in jobs]
        parts = [f.result() for f in futs]
    return merge(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--update-golden", action="store_true")
    ap.add_argument("--update-golden-ext", action="store_true", help="record JVM goldens for the extended probes (golden/<proj>.ext.json)")
    ap.add_argument("--ext", action="store_true", help="also run the extended probes (scored by nx/bin/nx-gate)")
    ap.add_argument("--server-cmd")
    ap.add_argument("--mova-bin", default=os.environ.get("MOVA_BIN", DEFAULT_MOVA))
    ap.add_argument("--budget", type=float, default=10)
    ap.add_argument("--full", action="store_true", help="all projects truly cold, bg cap 30 s, no budget")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--settled-only", action="store_true", help="cold settled server only: no early/await/warm checks (for non-Mova servers, see nx/bin/nx-gate)")
    args = ap.parse_args()
    t0, load0 = time.monotonic(), os.getloadavg()  # load before our servers start
    xdg = xdg_dir()
    xdg.mkdir(parents=True, exist_ok=True)
    errdir = Path(tempfile.mkdtemp(prefix="lsp-e2e-logs-"))
    names = [d.name for d in sorted((HERE / "projects").iterdir()) if (d / "e2e.json").exists()
             and (not args.only or d.name == args.only)]
    tmps = []
    try:
        if args.update_golden_ext:
            return update_golden_ext(args, [n for n in names if n != "mova"], xdg, tmps, errdir) or 0
        if args.update_golden:
            return update_golden(args, names, xdg, tmps, errdir) or 0
        results = collect(args, names, xdg, tmps, errdir)
        save_seed(results, xdg / "seed-cljs", xdg, args)
        return report(results, args, time.monotonic() - t0, load0)
    finally:
        for d in tmps:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
