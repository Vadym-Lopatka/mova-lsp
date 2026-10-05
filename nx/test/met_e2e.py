#!/usr/bin/env python3
"""Metrics end to end: python3 nx/test/met_e2e.py [--lib DIR] [--table]
A realistic session on a copy of lib (counts in the session file = what the client sent and got), two servers at once,
kill -9, a corrupt file, compact, the launcher env file, off; M2: job and pass stats, memory, startup stamps. Needs MOVA_BIN (with the native tap) and XDG_CACHE_HOME."""
import argparse, collections, os, re, shutil, signal, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lspc import C, uri

NX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STATS = os.path.join(NX, "bin", "nx-stats")
STATE = os.environ["XDG_STATE_HOME"]                           # private: set by nxenv (imported by lspc)
SESS = os.path.join(STATE, "nx", "sessions")
fails = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " " + str(info)))
    if not ok:
        fails.append(name)


def edn(s):
    """Reads the EDN subset of a session file: maps, vectors, keywords, strings, ints, nil, true, false."""
    toks = re.findall(r'"(?:[^"\\]|\\.)*"|[{}\[\]]|[^\s,{}\[\]]+', s)
    pos = [0]

    def rd():
        t = toks[pos[0]]; pos[0] += 1
        if t == "{":
            m = {}
            while toks[pos[0]] != "}":
                k = rd(); m[k] = rd()
            pos[0] += 1
            return m
        if t == "[":
            v = []
            while toks[pos[0]] != "]":
                v.append(rd())
            pos[0] += 1
            return v
        if t[0] == '"':
            return t[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return {"nil": None, "true": True, "false": False}.get(t, int(t) if re.fullmatch(r"-?\d+", t) else t)
    return rd()


class Cnt(C):
    """lspc client that counts what goes out and what comes in, per method."""
    def __init__(self, env=None):
        self.out, self.inn, self.replies = collections.Counter(), collections.Counter(), 0
        self.t_spawn, self.t_end = time.time(), None           # spawn; `$/progress` end seen (the project pass is over)
        old = dict(os.environ)
        for k, v in (env or {}).items():                       # None = unset
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        try:
            super().__init__()
        finally:
            os.environ.clear(); os.environ.update(old)

    def w(self, o):
        self.out[o.get("method", "client/response")] += 1
        super().w(o)

    def _h(self, m):
        if "method" in m:
            self.inn[m["method"]] += 1
            if m["method"] == "$/progress" and m["params"]["value"].get("kind") == "end" and self.t_end is None:
                self.t_end = time.time()
        else:
            self.replies += 1
        super()._h(m)

    def file(self):
        fs = [f for f in os.listdir(SESS) if f.endswith("-%d.edn" % self.pid())] if os.path.isdir(SESS) else []
        return os.path.join(SESS, fs[0]) if fs else None

    def pid(self):
        """The server's pid: nx-lsp execs Mova, so it is the child itself."""
        return self.p.pid

    def pic(self):
        f = self.file()
        return edn(open(f).read()) if f else None


def stats(*args):
    p = subprocess.run([STATS] + list(args), capture_output=True, text=True, timeout=30)
    return p.stdout, p.stderr


def footprint_b(pid):
    """phys_footprint in bytes from footprint(1), the outside measure (as nx/bench/lsp_bench.py)."""
    out = subprocess.run(["footprint", "--noCategories", "-f", "bytes", str(pid)], capture_output=True, text=True).stdout
    m = re.search(r"phys_footprint: (\d+) B", out)
    return int(m.group(1)) if m else None


def pings(c, n):
    for i in range(n):
        c.req("nx/ping", {"n": i})


def wait_for(pred, t):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def ping_count(c):
    p = c.pic()
    return ((p or {}).get(":ops", {}).get("nx/ping", {}).get(":count", 0)) if p else 0


def t_session(lib, table, in_place=False):
    tmp = tempfile.mkdtemp(prefix="nxmet")
    root = os.path.realpath(lib if in_place else tmp + "/lib")
    if not in_place:
        shutil.copytree(lib, root)
    c = Cnt({"NX_METRICS": "1"})
    c.req("initialize", {"processId": os.getpid(), "rootUri": "file://" + root, "workDoneToken": "tok",
                         "capabilities": {"textDocument": {"publishDiagnostics": {}}}}, 120)
    c.notify("initialized", {})
    c.quiet(1.5, 120)
    files = [root + "/src/clojure_lsp/" + f for f in ("handlers.clj", "queries.clj", "refactor/edit.clj")]
    texts = {}
    for f in files:
        texts[f] = open(f).read()
        c.notify("textDocument/didOpen", {"textDocument": {"uri": uri(f), "languageId": "clojure", "version": 1, "text": texts[f]}})
    c.quiet(1.0, 60)
    f = files[0]
    toks = [(ln, m.start() + 1) for ln, line in enumerate(texts[f].split("\n")) for m in re.finditer(r"[a-zA-Z][\w./*?!<>=+-]{2,}", line)]
    toks = toks[::max(1, len(toks) // 40)][:40]
    td = {"uri": uri(f)}
    for l, ch in toks:
        pos = {"textDocument": td, "position": {"line": l, "character": ch}}
        c.req("textDocument/hover", pos)
        c.req("textDocument/definition", pos)
        c.req("textDocument/completion", {"textDocument": td, "position": {"line": l, "character": ch + 2}})
        c.req("textDocument/references", dict(pos, context={"includeDeclaration": True}))
    for v in range(2, 8):                                      # edits -> diagnostics
        n = len(c.pubs)
        texts[f] += "\n(def nx-met-%d (inc :x))\n" % v
        c.notify("textDocument/didChange", {"textDocument": {"uri": uri(f), "version": v}, "contentChanges": [{"text": texts[f]}]})
        wait_for(lambda: len(c.pubs) > n, 20)
        time.sleep(0.05)
    c.req("workspace/executeCommand", {"command": "clean-ns", "arguments": [uri(f), 0, 0]})
    c.req("workspace/executeCommand", {"command": "no-such-command", "arguments": []})
    c.req("foo/bar", {})
    c.notify("textDocument/didClose", {"textDocument": {"uri": uri(files[2])}})
    c.quiet(1.0, 30)
    path = c.file()
    ext_b = footprint_b(c.pid())                              # footprint(1) just before shutdown
    c.close()
    p = c.pic()
    check("session: file exists, ended", bool(p) and p.get(":ended") is True, path)
    ops = p[":ops"]
    native = {op: ops.pop(op) for op in ("job/open", "job/bg", "bg/pass") if op in ops}
    t_native(c, p, native, ext_b, table)
    key = lambda m, a=None: m
    sent = collections.Counter()
    for m, n in c.out.items():
        sent[m] += n
    # the two commands are their own ops
    sent["workspace/executeCommand"] -= 2; sent["cmd/clean-ns"] += 1; sent["cmd/other"] += 1
    sent["foo/bar"] -= 1; sent["other"] += 1
    sent = {m: n for m, n in sent.items() if n}
    got = {op: e.get(":count", 0) for op, e in ops.items() if e.get(":count")}
    check("session: counts in = sent, per op", got == sent, {k: (got.get(k), sent.get(k)) for k in set(got) | set(sent) if got.get(k) != sent.get(k)})
    outs = {op: e.get(":sent", 0) for op, e in ops.items() if e.get(":sent")}
    check("session: counts out = received, per method", outs == dict(c.inn), {k: (outs.get(k), c.inn.get(k)) for k in set(outs) | set(c.inn) if outs.get(k) != c.inn.get(k)})
    joined = sum(sum(e.get(":outcomes", {}).values()) for op, e in ops.items() if op != "pipe/diagnostics")
    check("session: joined replies = replies received", joined == c.replies, (joined, c.replies))
    d = ops.get("pipe/diagnostics", {}).get(":outcomes", {}).get(":ok", 0)
    check("session: edits joined to diagnostics", 6 <= d <= 9, d)
    check("session: hover 40, each with a sample", ops["textDocument/hover"][":stages"][":m/inject-us"][":count"] == 40)
    check("session: unknown method is an error under \"other\"", ops.get("other", {}).get(":outcomes") == {":error": 1}, ops.get("other"))
    check("session: lost 0, unanswered 0, fold errors 0", (p[":lost"], p[":unanswered"], p[":fold-errors"]) == (0, 0, 0), p.get(":lost-by"))
    check("session: config root + pid", p[":config"][":root"] == "file://" + root and p[":config"][":pid"] == c.pid(), p[":config"])
    check("session: no document text in the file", "nx-met-2" not in open(path).read())
    out, err = stats("stats", "--session", str(c.pid()))
    check("nx-stats stats prints the session", "textDocument/hover" in out and "pipe/diagnostics" in out and not err, err)
    check("nx-stats stats: job and pass rows, pass counters", all(x in out for x in ("job/bg queue", "job/bg read", "job/open analyze", "bg/pass pass", "passes 1 | files ")), out[:400])
    st, _ = stats("status", "--session", str(c.pid()))
    check("nx-stats status: memory and startup of the session", "mem MB init " in st and "| startup spawn " in st and " settled " in st, st)
    if table:
        print(out)
        print(st)
    shutil.rmtree(tmp, ignore_errors=True)


def t_native(c, p, native, ext_b, table):
    """M2: worker jobs, the project pass, memory, startup; two numbers checked against an outside measure."""
    bgj, opj, ps, bg = native.get("job/bg", {}), native.get("job/open", {}), native.get("bg/pass", {}), p.get(":bg", {})
    n = lambda e, k: e.get(":stages", {}).get(k, {}).get(":count", 0)
    last = bg.get(":last", {})
    check("jobs: every project file is a job/bg with queue, read, parse, analyze", last.get(":files", 0) > 100 and last[":files"] <= bgj.get(":count", 0) <= last[":files"] + 3
          and all(n(bgj, k) == bgj[":count"] for k in (":m/queue-us", ":m/read-us", ":m/parse-us", ":m/analyze-us")) and bgj[":bytes"] > 1000000, (last, bgj.get(":count")))
    check("jobs: opens and edits are job/open, no read stage", opj.get(":count", 0) >= 9 and n(opj, ":m/read-us") == 0
          and all(n(opj, k) == opj[":count"] for k in (":m/queue-us", ":m/parse-us", ":m/analyze-us")) and opj[":bytes"] > 0, opj.get(":count"))
    sp = {k: v[":sum-us"] for k, v in ps.get(":stages", {}).items()}
    check("pass: one record, all spans, parts fit in the whole", ps.get(":count") == 1 and bg.get(":passes") == 1 and len(sp) == 8
          and sp[":m/files-us"] <= sp[":m/pass-us"]
          and sum(sp[k] for k in sp if k not in (":m/pass-us",)) <= sp[":m/pass-us"] * 1.02 + 2000, sp)
    check("pass: jar counters add up", last.get(":jars", -1) == last.get(":jars-warm", 0) + last.get(":jars-cold", 0) + last.get(":jars-failed", 0)
          and last.get(":cp-cached") in (0, 1) and bg.get(":total") == last, last)
    mem, su = p.get(":mem", {}), p.get(":startup", {})
    mb = 1 << 20
    check("mem: init <= max, settled and last known", all(5 * mb < mem.get(k, 0) < 4096 * mb for k in (":init-b", ":settled-b", ":max-b", ":last-b"))
          and max(mem[":init-b"], mem[":settled-b"], mem[":last-b"]) <= mem[":max-b"], mem)
    order = [su.get(k) for k in (":spawn-us", ":main-us", ":initialize-in-us", ":initialize-out-us", ":settled-us", ":progress-end-us")]
    check("startup: stamps in order from the process start", all(isinstance(x, int) for x in order) and order == sorted(order) and 0 <= order[0] and order[-1] < 120e6, su)
    live_b = mem.get(":last-b", 0)                             # read by the server at exit
    check("cross-check: footprint at exit vs footprint(1) just before shutdown within 1 MB", ext_b is not None and abs(live_b - ext_b) <= mb, (live_b, ext_b))
    client_us = ((c.t_end or 0) - c.t_spawn) * 1e6
    check("cross-check: progress-end stamp vs the client's spawn -> progress end within 20 ms", c.t_end is not None and -5e3 <= client_us - su.get(":progress-end-us", 0) <= 20e3, (client_us, su.get(":progress-end-us")))
    if table:
        print("cross-check: footprint server %.2f MB, footprint(1) %.2f MB | progress end server %.1f ms, client spawn->progress end %.1f ms"
              % (live_b / 1e6, (ext_b or 0) / 1e6, su.get(":progress-end-us", 0) / 1e3, client_us / 1e3))


def t_two():
    a, b = Cnt({"NX_METRICS": "1"}), Cnt({"NX_METRICS": "1"})
    a.init("/nonexistent-a"); b.init("/nonexistent-b")
    pings(a, 30); pings(b, 50)
    ok = wait_for(lambda: ping_count(a) == 30 and ping_count(b) == 50, 3)
    check("two servers: two files, each its own counts (written while live)", ok and a.file() != b.file(), (ping_count(a), ping_count(b)))
    out, _ = stats("status")
    check("two servers: status shows both live", len(re.findall(r" live ", out)) >= 2, out)
    a.close(); b.close()
    out, _ = stats("latency", "--root", "nonexistent-")
    p = edn(out)
    check("two servers: merged counts", (p[":sessions"], p[":ops"]["nx/ping"][":count"], p[":ops"]["nx/ping"][":stages"][":m/inject-us"][":count"]) == (2, 80, 80), out[:300])
    out, _ = stats("stats", "--by-root", "--root", "nonexistent-")
    check("two servers: --by-root gives one table per root", out.count("== root") == 2, out[:300])


def t_kill():
    c = Cnt({"NX_METRICS": "1"})
    c.init("/nonexistent-kill")
    pings(c, 10)
    check("kill: first write after one quiet tick", wait_for(lambda: ping_count(c) == 10, 1.0), ping_count(c))
    time.sleep(0.5)
    t = time.time()
    pings(c, 10)
    ok = wait_for(lambda: ping_count(c) == 20, 6.0)
    dt = time.time() - t
    check("kill: next write within 5 s + one quiet tick", ok and dt <= 5.3, "%.2f s" % dt)
    pings(c, 5)                                                # lost with the process: written < 5 s ago
    f, pid = c.file(), c.pid()
    os.kill(pid, signal.SIGKILL); c.p.wait()
    p = edn(open(f).read())
    check("kill -9: the file stays, not ended, at most one write old", p[":ended"] is False and p[":ops"]["nx/ping"][":count"] == 20, p[":ops"]["nx/ping"][":count"])
    out, _ = stats("status", "--session", str(pid))
    check("kill -9: status says dead", " dead " in out, out)


def t_corrupt_compact():
    for nm, body in (("1-1.edn", "{:v 1 :ops {\"nx/ping\" "), ("2-1.edn", "\x00\x01 garbage"), ("3-1.edn", "{:v 1 :ops 5}")):
        open(os.path.join(SESS, nm), "w").write(body)
    out, err = stats("stats")
    check("corrupt: skipped with one warning", err.count("\n") == 1 and "skipped 3 corrupt file(s): 1-1.edn 2-1.edn 3-1.edn" in err and "nx/ping" in out, err)
    live = Cnt({"NX_METRICS": "1"})
    live.init("/nonexistent-live")
    pings(live, 7)
    wait_for(lambda: ping_count(live) == 7, 2)
    before = edn(stats("latency")[0])
    n_files = len([f for f in os.listdir(SESS) if f.endswith(".edn")])
    out, err = stats("compact")
    left = sorted(f for f in os.listdir(SESS) if f.endswith(".edn"))
    check("compact: only the live session and the corrupt files are left", len(left) == 4 and os.path.basename(live.file()) in left, (out, left))
    check("compact: says what it did", "compacted %d session(s)" % (n_files - 4) in out and "1 live left" in out, out)
    after = edn(stats("latency", "--all")[0])
    same = lambda p: (p[":sessions"], p[":lost"], {op: (e.get(":count"), e.get(":sent"), e.get(":outcomes"), e.get(":stages")) for op, e in p[":ops"].items()})
    check("compact: lifetime + live = the picture before", same(before) == same(after), (before[":sessions"], after[":sessions"]))
    only_live = edn(stats("latency")[0])
    check("compact: without --all only the live session", only_live[":sessions"] == 1 and only_live[":ops"]["nx/ping"][":count"] == 7)
    pings(live, 3)
    out, _ = stats("compact")
    check("compact again: nothing to do, live untouched", "compacted 0 session(s)" in out and live.file() is not None, out)
    live.close()
    stats("compact")
    final = edn(stats("latency", "--all")[0])
    check("compact after exit: live session folded once", final[":ops"]["nx/ping"][":count"] == before[":ops"]["nx/ping"][":count"] + 3
          and len([f for f in os.listdir(SESS) if f.endswith(".edn")]) == 3, final[":ops"]["nx/ping"][":count"])
    for nm in ("1-1.edn", "2-1.edn", "3-1.edn"):
        os.remove(os.path.join(SESS, nm))


def t_launcher():
    cfg = tempfile.mkdtemp(prefix="nxcfg")
    os.makedirs(cfg + "/nx")
    open(cfg + "/nx/env", "w").write("# owner defaults\nNX_METRICS=1\nMOVA_SHARDS=7\nPATH=/nope\nNX_BAD KEY=1\n")
    n0 = len(os.listdir(SESS))
    c = Cnt({"NX_METRICS": None, "XDG_CONFIG_HOME": cfg}); c.init("/nonexistent-env"); pings(c, 3); c.close()
    p = c.pic()
    check("launcher: env file turns metrics on; only NX_* keys are read", bool(p) and p[":ops"]["nx/ping"][":count"] == 3 and p[":config"][":shards"] == 2, p and p[":config"])
    c = Cnt({"NX_METRICS": "0", "XDG_CONFIG_HOME": cfg}); c.init("/nonexistent-env"); pings(c, 3); c.close()
    check("launcher: an explicit variable wins over the env file", c.file() is None)
    c = Cnt({"NX_METRICS": None}); c.init("/nonexistent-env"); pings(c, 3); c.close()
    check("off (no env file): no session file", c.file() is None and len(os.listdir(SESS)) == n0 + 1)
    shutil.rmtree(cfg, ignore_errors=True)


def journal_of(c):
    fs = [f for f in os.listdir(SESS) if f.endswith("-%d.log" % c.pid())] if os.path.isdir(SESS) else []
    return os.path.join(SESS, fs[0]) if fs else None


def t_journal():
    """M3: NX_OBS=1 writes one line per event next to the session file; off writes none."""
    u = "file:///nonexistent-obs/a.clj"
    t0 = time.time() * 1000
    c = Cnt({"NX_METRICS": "1", "NX_OBS": "1"})
    c.init("/nonexistent-obs")
    c.notify("textDocument/didOpen", {"textDocument": {"uri": u, "languageId": "clojure", "version": 1, "text": "(ns a) ; SECRET-TEXT-42\n(inc :x)\n"}})
    pings(c, 25)
    c.req("textDocument/hover", {"textDocument": {"uri": u}, "position": {"line": 0, "character": 2}, "secret": "SECRET-PARAM-7"})
    c.req("foo/bar", {})
    c.close()
    t1 = time.time() * 1000
    log = journal_of(c)
    check("journal: file next to the session file", bool(log) and log[:-4] == c.file()[:-4], log)
    text = open(log).read()
    rows = [edn(l) for l in text.splitlines()]
    kinds = collections.Counter(r.get(":tel/kind") for r in rows)
    reqs = [r for r in rows if r.get(":tel/kind") == ":request"]
    check("journal: one request row per request sent (replies joined by id)", len(reqs) == c.id and len({r[":id"] for r in reqs}) == c.id, (len(reqs), c.id))
    check("journal: 25 pings, each with outcome and :m/inject-us", sum(r[":op"] == "nx/ping" for r in reqs) == 25
          and all(r[":outcome"] in (":ok", ":null", ":error") and isinstance(r[":m/inject-us"], int) and r[":m/inject-us"] >= 0 for r in reqs), kinds)
    check("journal: error reply and bytes", [r[":outcome"] for r in reqs if r[":op"] == "other"] == [":error"] and all(r[":bytes"] > 0 for r in reqs))
    check("journal: didOpen notification with its uri", any(r.get(":op") == "textDocument/didOpen" and r.get(":uri") == u and r.get(":tel/kind") == ":notify" for r in rows))
    check("journal: first row session start, last row session end", rows[0].get(":phase") == "start" and rows[-1].get(":phase") == "end", (rows[0], rows[-1]))
    check("journal: wall times inside the run", all(t0 - 5000 <= r[":at"] <= t1 + 2000 for r in rows), (t0, t1, min(r[":at"] for r in rows), max(r[":at"] for r in rows)))
    check("journal: no document text, no params", "SECRET" not in text and "(inc :x)" not in text)
    p = c.pic()
    got = sum(sum(e.get(":outcomes", {}).values()) for op, e in p[":ops"].items() if op != "pipe/diagnostics")
    check("journal: rows = the picture's joined requests", got == len(reqs), (got, len(reqs)))
    out, err = stats("tail", "--session", str(c.pid()), "-n", "3")
    lines = out.splitlines()
    check("tail: header + the last 3 lines, readable", len(lines) == 4 and lines[0].startswith("time(UTC)") and "session" in lines[-1] and not err, out)
    out, _ = stats("tail")
    check("tail: default = newest session, 20 lines", len(out.splitlines()) <= 21 and "nx/ping" in out or "textDocument/hover" in out, out)
    out, _ = stats("tail", "-n", "0")
    check("tail: bad -n prints usage", out.startswith("usage:"), out[:60])
    # follow a live server: sees a new line within a second, stops after the session end
    live = Cnt({"NX_METRICS": "1", "NX_OBS": "1"})
    live.init("/nonexistent-obs-live")
    pings(live, 3)
    wait_for(lambda: journal_of(live) and "nx/ping" in open(journal_of(live)).read(), 3)
    f = subprocess.Popen([STATS, "tail", "-f", "-n", "2", "--session", str(live.pid())], stdout=subprocess.PIPE, text=True, bufsize=1)
    time.sleep(0.8)
    live.req("textDocument/hover", {"textDocument": {"uri": u}, "position": {"line": 0, "character": 0}})
    live.quiet(0.5, 5)
    time.sleep(0.5)
    live.close()
    try:
        out, _ = f.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        f.kill(); out, _ = f.communicate()
    check("tail -f: follows a live journal and ends with the session", "textDocument/hover" in out and out.rstrip().endswith("phase=\"end\"") and f.returncode == 0, out[-300:])
    # rotation: a small bound, one kept
    r = Cnt({"NX_METRICS": "1", "NX_OBS": "1", "NX_OBS_MAX_BYTES": "3000"})
    r.init("/nonexistent-obs-rot")
    for _ in range(4):
        pings(r, 40); time.sleep(0.4)
    r.close()
    rl = journal_of(r)
    old = rl + ".1"
    check("journal: rotation keeps the live file and one .log.1", os.path.exists(old) and os.path.exists(rl) and not os.path.exists(rl + ".2"), os.listdir(SESS))
    check("journal: rotated files hold whole lines; the live one ends with the session end", all(l.startswith("{") and l.endswith("}") for l in open(old).read().splitlines())
          and edn(open(rl).read().splitlines()[-1]).get(":phase") == "end")
    # off: metrics on without NX_OBS, and NX_OBS without metrics, write no journal
    a = Cnt({"NX_METRICS": "1"}); a.init("/nonexistent-obs-off"); pings(a, 5); a.close()
    b = Cnt({"NX_METRICS": None, "NX_OBS": "1"}); b.init("/nonexistent-obs-off"); pings(b, 5); b.close()
    check("journal off: no .log without NX_OBS, none without NX_METRICS", journal_of(a) is None and journal_of(b) is None and a.file() is not None and b.file() is None)
    out, _ = stats("tail", "--session", str(a.pid()))
    check("tail: a session without a journal says so", "no journal" in out, out)
    # compact removes the journal (and .log.1) of the sessions it folds
    open(log + ".1", "w").write("{:at 1}\n")
    cf = c.file()
    out, _ = stats("compact")
    check("compact: removes the journals of compacted sessions", not any(os.path.exists(x) for x in (log, log + ".1", old, rl)) and not os.path.exists(cf), os.listdir(SESS))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=os.path.join(NX, "..", "lib"))
    ap.add_argument("--table", action="store_true", help="print the nx-stats table of the realistic session")
    ap.add_argument("--in-place", action="store_true", help="the session runs on --lib itself (its deps resolve: jars, classpath), not on a copy")
    a = ap.parse_args()
    t_session(os.path.abspath(a.lib), a.table, a.in_place)
    t_two()
    t_kill()
    t_corrupt_compact()
    t_launcher()
    t_journal()
    print("met_e2e: %s" % ("ok" if not fails else "FAILED " + ", ".join(fails)))
    sys.exit(1 if fails else 0)
