#!/usr/bin/env python3
"""nx spikes, end to end over stdio. python3 stdlib only.
  lsp_bench.py init  [--runs 10]   spawn -> initialize reply ms + phys_footprint MB, cold and warm image
  lsp_bench.py sparse [--gap-ms 50] [--n 200] [--met]        one request every gap ms, RTT of each
  lsp_bench.py hop   [--n 1000] [--met] [--only-pingpong]   ping-pong RTT, pipelined latency/throughput, didOpen -> publishDiagnostics
                                   (--met: run with NX_METRICS=1; prints what the server folded, from its session file)
  lsp_bench.py query [--n 200] [--lib DIR]   per-method request RTT + footprint on a settled project (default lib/)
  lsp_bench.py idle  [--secs 30]   threads, footprint, CPU at idle
  lsp_bench.py pipe  [--runs 10] [--lib DIR]  pipeline: init reply, first diagnostics (cold/warm), bg pass over a fresh copy of DIR
                                   (progress end, footprint peak/end), didChange -> diagnostics on a 90 KB file
Server: --server nx/bin/nx-lsp (default). XDG_CACHE_HOME is taken from env (cold = its nx/ dir is removed)."""
import argparse, json, os, queue, re, shutil, statistics, subprocess, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test")); import nxenv  # private XDG_STATE_HOME / XDG_CONFIG_HOME for every spawned server

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SERVER = os.path.join(HERE, "..", "bin", "nx-lsp")


def frame(obj):
    body = json.dumps(obj, separators=(",", ":")).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def tree_pids(root_pid):
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    ch = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) == 2:
            ch.setdefault(int(p[1]), []).append(int(p[0]))
    pids, st = [], [root_pid]
    while st:
        x = st.pop()
        if x not in pids:
            pids.append(x)
            st.extend(ch.get(x, []))
    return pids


def footprint_mb(pid):
    """phys_footprint (MB) summed over the process tree (macOS `footprint`, as in mova/smoke/lsp_client.py)."""
    out = subprocess.run(["footprint", "--noCategories", "-f", "bytes"] + [str(p) for p in tree_pids(pid)],
                         capture_output=True, text=True, timeout=10).stdout
    return sum(int(m) for m in re.findall(r"phys_footprint: (\d+) B", out)) / 1e6


def threads(pid):
    out = subprocess.run(["ps", "-M", str(pid)], capture_output=True, text=True).stdout
    return max(len(out.strip().splitlines()) - 1, 0)  # minus header


def cputime_s(pid):
    t = subprocess.run(["ps", "-o", "cputime=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    m, s = t.split(":")
    return int(m) * 60 + float(s)


def load():
    return os.getloadavg()[0]


class Server:
    def __init__(self, cmd, errfile=None):
        self.t0 = time.perf_counter()
        self.p = subprocess.Popen(cmd if isinstance(cmd, list) else [cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errfile or subprocess.DEVNULL, bufsize=0)
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        f = self.p.stdout
        buf = b""
        while True:
            chunk = os.read(f.fileno(), 65536)
            if not chunk:
                self.q.put((time.perf_counter(), None))
                return
            buf += chunk
            while True:
                i = buf.find(b"\r\n\r\n")
                if i < 0:
                    break
                n = int(re.search(rb"Content-Length: (\d+)", buf[:i]).group(1))
                if len(buf) < i + 4 + n:
                    break
                body = buf[i + 4:i + 4 + n]
                buf = buf[i + 4 + n:]
                self.q.put((time.perf_counter(), json.loads(body)))

    def send(self, obj):
        self.p.stdin.write(frame(obj))

    def recv(self, timeout=30):
        return self.q.get(timeout=timeout)

    def wait_id(self, i):
        while True:
            t, m = self.recv()
            if m is None:
                raise RuntimeError("server closed")
            if m.get("id") == i and "method" not in m:
                return t, m

    def initialize(self, root=None, token=None):
        p = {"processId": os.getpid(), "rootUri": ("file://" + root) if root else None, "capabilities": {}}
        if token:
            p["workDoneToken"] = token
        self.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": p})
        t, m = self.wait_id(0)
        self.t_init = time.perf_counter()
        self.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        return (t - self.t0) * 1000, m

    def close(self):
        try:
            self.send({"jsonrpc": "2.0", "id": 99999, "method": "shutdown"})
            self.wait_id(99999)
            self.send({"jsonrpc": "2.0", "method": "exit"})
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


def cache_dir():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "nx")


def cmd_init(a):
    for mode in ("cold", "warm"):
        if mode == "warm":
            s = Server(a.server); s.initialize(); s.close()  # make sure the image exists
        ms, mb = [], []
        for _ in range(a.runs):
            if mode == "cold":
                shutil.rmtree(cache_dir(), ignore_errors=True)
            s = Server(a.server)
            t, _ = s.initialize()
            time.sleep(0.2)
            ms.append(t); mb.append(footprint_mb(s.p.pid))
            s.close()
        print(f"init {mode}: runs={a.runs} ms p50={statistics.median(ms):.2f} max={max(ms):.1f} | footprint MB p50={statistics.median(mb):.2f} max={max(mb):.1f} | load={load():.1f}")


def met_report(sent):
    """What the server folded (NX_METRICS=1), from its session file: joined pings and diagnostics, taps lost."""
    import glob
    fs = sorted(glob.glob(os.path.join(os.environ["XDG_STATE_HOME"], "nx", "sessions", "*.edn")), key=os.path.getmtime)
    txt = open(fs[-1]).read() if fs else ""
    def op(name, key="stages"):
        m = re.search(r'"%s"\n\s+(\{.*)' % re.escape(name), txt)
        line = m.group(1) if m else ""
        j = re.search(r":m/inject-us \{:count (\d+), :sum-us (\d+), :max-us (\d+)", line)
        c = re.search(r"^\{:count (\d+)", line)
        return (int(c.group(1)) if c else 0, [int(x) for x in j.groups()] if j else [0, 0, 0])
    one = lambda pat: (re.search(pat, txt) or [None, "?"])[1]
    seen, (n, sm, mx) = op("nx/ping")
    print(f"met: sent pings={sent} | seen={seen} joined={n} diag_joined={op('pipe/diagnostics')[1][0]} lost={one(r':lost (\d+)')} "
          f"lost-by={{{one(r':lost-by [{]([^}]*)[}]')}}} unanswered={one(r':unanswered (\d+)')} fold_errors={one(r':fold-errors (\d+)')} "
          f"ended={one(r':ended (\w+)')} files={len(fs)}")
    if n:
        print(f"met: nx/ping m/inject-us mean={sm / n:.1f} us max={mx} us")


def cmd_hop(a):
    s = Server(a.server); s.initialize()
    n = a.n
    ping = lambda i: {"jsonrpc": "2.0", "id": i, "method": "nx/ping", "params": {"n": i}}
    for i in range(1, 101):  # warm-up
        s.send(ping(i)); s.wait_id(i)
    # ping-pong RTT, one in flight
    rtt = []
    for i in range(1000, 1000 + n):
        t = time.perf_counter(); s.send(ping(i)); t1, _ = s.wait_id(i); rtt.append((t1 - t) * 1000)
    print(f"ping-pong n={n}: RTT ms p50={pct(rtt,50):.4f} p99={pct(rtt,99):.4f} max={max(rtt):.3f}")
    if a.only_pingpong:
        s.close()
        if a.met:
            met_report(100 + n)
        return
    # pipelined: all requests in one burst
    base = 10000
    sent = {}
    t_start = time.perf_counter()
    # send in a thread so the stdin pipe never blocks the reader
    def sender():
        for i in range(n):
            j = base + i
            sent[j] = time.perf_counter(); s.send(ping(j))
    th = threading.Thread(target=sender); th.start()
    lat, got = [], 0
    while got < n:
        t, m = s.recv()
        if m and m.get("id", 0) >= base and "result" in m:
            lat.append((t - sent[m["id"]]) * 1000); got += 1
    total = time.perf_counter() - t_start
    th.join()
    print(f"pipelined n={n}: latency ms p50={pct(lat,50):.3f} p99={pct(lat,99):.3f} | throughput {n/total:.0f} req/s ({total*1000:.0f} ms total)")
    # didOpen -> publishDiagnostics
    text = "(ns a)\n" + "(defn f [x] (inc x))\n" * 200
    d = []
    for i in range(n // 10):
        uri = f"file:///b/{i}.clj"
        t = time.perf_counter()
        s.send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "clojure", "version": 1, "text": text}}})
        while True:
            t1, m = s.recv()
            if m and m.get("method") == "textDocument/publishDiagnostics":
                break
        d.append((t1 - t) * 1000)
    print(f"didOpen->publishDiagnostics n={len(d)}: ms p50={pct(d,50):.3f} p99={pct(d,99):.3f} max={max(d):.3f} | load={load():.1f}")
    s.close()
    if a.met:
        met_report(100 + 2 * n)


def cmd_sparse(a):
    """One request every --gap-ms (the server is idle between them): RTT of each. The editor pattern."""
    s = Server(a.server); s.initialize()
    ping = lambda i: {"jsonrpc": "2.0", "id": i, "method": "nx/ping", "params": {"n": i}}
    for i in range(1, 21):
        s.send(ping(i)); s.wait_id(i)
    rtt = []
    for i in range(1000, 1000 + a.n):
        time.sleep(a.gap_ms / 1000)
        t = time.perf_counter(); s.send(ping(i)); t1, _ = s.wait_id(i); rtt.append((t1 - t) * 1000)
    print(f"sparse gap={a.gap_ms}ms n={a.n}: RTT ms p50={pct(rtt,50):.4f} p90={pct(rtt,90):.4f} p99={pct(rtt,99):.4f} max={max(rtt):.3f} | load={load():.1f}")
    s.close()
    if a.met:
        met_report(20 + a.n)


def is_pub(m):
    return m and m.get("method") == "textDocument/publishDiagnostics"


def is_end(m):
    return m and m.get("method") == "$/progress" and m["params"]["value"]["kind"] == "end"


def open_msg(uri, text, v=1):
    return {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "clojure", "version": v, "text": text}}}


def fresh_copy(lib):
    dst = tempfile.mkdtemp(prefix="nxlib")
    shutil.copytree(lib, dst + "/lib")
    open(dst + "/lib/deps.edn", "a").close()
    return dst + "/lib"


def cmd_pipe(a):
    lib = os.path.abspath(a.lib)
    root = fresh_copy(lib)
    target = os.path.join(root, "src/clojure_lsp/handlers.clj")
    big = os.path.join(root, "src/clojure_lsp/refactor/transform.clj")
    htext, btext = open(target).read(), open(big).read()
    print(f"# load={load():.1f} lib={len(list(os.walk(root)))} dirs, handlers {len(htext)} B, big {len(btext)} B, workers={os.environ.get('NX_WORKERS','default')}")
    for mode in ("cold", "warm"):
        if mode == "warm":
            s = Server(a.server); s.initialize(); s.close()
        init_ms, first_ms = [], []
        for _ in range(a.runs):
            if mode == "cold":
                shutil.rmtree(cache_dir(), ignore_errors=True)
            s = Server(a.server)
            t, _ = s.initialize(root)
            s.send(open_msg("file://" + target, htext))
            while True:
                t1, m = s.recv()
                if is_pub(m):
                    break
            init_ms.append(t); first_ms.append((t1 - s.t0) * 1000)
            s.close()
        print(f"{mode}: spawn->initialize reply ms p50={statistics.median(init_ms):.1f} max={max(init_ms):.1f} | spawn->first diagnostics (handlers.clj opened after initialized, bg pass running) ms p50={statistics.median(first_ms):.1f} max={max(first_ms):.1f}")
    # background pass, warm image, nothing opened
    end_ms, peaks, ends, cpu = [], [], [], []
    for _ in range(a.runs):
        s = Server(a.server)
        s.initialize(root, "tok")
        pk, stop = [0.0], threading.Event()
        def sampler():
            while not stop.is_set():
                try: pk[0] = max(pk[0], footprint_mb(s.p.pid))
                except Exception: pass
        th = threading.Thread(target=sampler, daemon=True); th.start()
        while True:
            t1, m = s.recv(60)
            if is_end(m):
                break
        end_ms.append((t1 - s.t_init) * 1000)
        stop.set(); th.join()
        time.sleep(0.3)
        peaks.append(pk[0]); ends.append(footprint_mb(s.p.pid)); cpu.append(cputime_s(s.p.pid))
        s.close()
    print(f"bg pass: initialized->progress end ms p50={statistics.median(end_ms):.1f} max={max(end_ms):.1f} | footprint MB peak(sampled) p50={statistics.median(peaks):.1f} end p50={statistics.median(ends):.1f} | cpu s p50={statistics.median(cpu):.2f} | load={load():.1f}")
    # didChange -> diagnostics on a 90 KB file
    s = Server(a.server); s.initialize(root, "tok")
    while not is_end(s.recv(60)[1]): pass
    uri = "file://" + big
    s.send(open_msg(uri, btext))
    while not is_pub(s.recv()[1]): pass
    lat = []
    for i in range(2, 2 + 2 * a.runs + 4):
        txt = btext + (")" if i % 2 == 0 else "")   # toggles one syntax error: diagnostics change every time
        t = time.perf_counter()
        s.send({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": uri, "version": i}, "contentChanges": [{"text": txt}]}})
        while not is_pub(s.recv()[1]): pass
        lat.append((time.perf_counter() - t) * 1000)
    lat = lat[4:]
    print(f"didChange->diagnostics {len(btext)//1000} KB (full sync): ms p50={statistics.median(lat):.2f} max={max(lat):.2f} n={len(lat)} | load={load():.1f}")
    s.close()
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)


def cmd_query(a):
    """Feature latency over stdio on a settled project: RTT per method (one request at a time) + footprint."""
    lib = os.path.abspath(a.lib)
    root = fresh_copy(lib)
    target = os.path.join(root, "src/clojure_lsp/handlers.clj")
    text = open(target).read()
    # sample symbol tokens (line, char) across the file
    toks = []
    for ln, line in enumerate(text.split("\n")):
        for m in re.finditer(r"[a-zA-Z][\w./*?!<>=+-]{2,}", line):
            toks.append((ln, m.start() + 1, m.group(0)))
    step = max(1, len(toks) // a.n)
    toks = toks[::step][: a.n]
    print(f"# load={load():.1f} lib handlers.clj {len(text)} B, {len(toks)} sampled positions, workers={os.environ.get('NX_WORKERS','default')}")
    s = Server(a.server)
    s.initialize(root, "tok")
    t_end = None
    while True:
        t1, m = s.recv(120)
        if is_end(m):
            break
    t_settled = (t1 - s.t_init) * 1000
    uri = "file://" + target
    s.send(open_msg(uri, text))
    while not is_pub(s.recv(60)[1]):
        pass
    time.sleep(0.3)
    fp = footprint_mb(s.p.pid)
    print(f"settled: initialized->progress end {t_settled:.0f} ms | footprint after settle+open {fp:.1f} MB | threads {threads(s.p.pid)}")
    nid = [1000]
    def rtt(method, params):
        nid[0] += 1
        t = time.perf_counter()
        s.send({"jsonrpc": "2.0", "id": nid[0], "method": method, "params": params})
        _, r = s.wait_id(nid[0])
        return (time.perf_counter() - t) * 1000, r
    td = {"uri": uri}
    methods = [
        ("definition", lambda l, c: {"textDocument": td, "position": {"line": l, "character": c}}),
        ("hover", lambda l, c: {"textDocument": td, "position": {"line": l, "character": c}}),
        ("references", lambda l, c: {"textDocument": td, "position": {"line": l, "character": c}, "context": {"includeDeclaration": True}}),
        ("documentHighlight", lambda l, c: {"textDocument": td, "position": {"line": l, "character": c}}),
        ("completion", lambda l, c: {"textDocument": td, "position": {"line": l, "character": c + 2}}),
        ("documentSymbol", lambda l, c: {"textDocument": td}),
    ]
    print(f"{'method':18} {'n':>4} {'p50 ms':>8} {'p90 ms':>8} {'p99 ms':>8} {'max ms':>8}  non-null")
    for name, mk in methods:
        for l, c, _ in toks[:5]:
            rtt("textDocument/" + name, mk(l, c))  # warm-up
        ts, nn = [], 0
        for l, c, _ in toks:
            t, r = rtt("textDocument/" + name, mk(l, c))
            ts.append(t)
            res = r.get("result")
            nn += 1 if res not in (None, [], {}) else 0
        print(f"{name:18} {len(ts):4d} {statistics.median(ts):8.3f} {pct(ts, 90):8.3f} {pct(ts, 99):8.3f} {max(ts):8.3f}  {nn}")
    print(f"footprint after queries {footprint_mb(s.p.pid):.1f} MB")
    time.sleep(3)                                              # metrics on: the taps are folded by now
    print(f"footprint 3 s later {footprint_mb(s.p.pid):.1f} MB")
    s.close()
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)


def cmd_idle(a):
    s = Server(a.server); s.initialize()
    time.sleep(1)
    c0 = cputime_s(s.p.pid); t0 = time.time()
    th0 = threads(s.p.pid); f0 = footprint_mb(s.p.pid)
    time.sleep(a.secs)
    c1 = cputime_s(s.p.pid); dt = time.time() - t0
    print(f"idle {a.secs}s: threads {th0} -> {threads(s.p.pid)} | footprint MB {f0:.1f} -> {footprint_mb(s.p.pid):.1f} | cpu {c1-c0:.2f}s over {dt:.0f}s ({100*(c1-c0)/dt:.2f}%) | load={load():.1f}")
    s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init", "hop", "sparse", "idle", "pipe", "query"])
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--gap-ms", type=float, default=50)
    ap.add_argument("--met", action="store_true")
    ap.add_argument("--only-pingpong", action="store_true")
    ap.add_argument("--lib", default=os.path.join(HERE, "..", "..", "lib"))
    a = ap.parse_args()
    {"init": cmd_init, "hop": cmd_hop, "sparse": cmd_sparse, "idle": cmd_idle, "pipe": cmd_pipe, "query": cmd_query}[a.cmd](a)
