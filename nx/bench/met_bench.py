#!/usr/bin/env python3
"""Metrics cost gate: base (run twice: A/A noise) vs this tree off vs on (NX_METRICS=1), runs rotated.
Drives lsp_bench.py. Pass = on - base and off - base inside the A/A spread.
  met_bench.py hop   [--rounds 12] [--n 1000] [--only-pingpong]   ping-pong p50/p99 (median over rounds), pipelined
                                                                  req/s, what the server folded and lost
  met_bench.py sparse [--gap-ms 50] [--n 200] [--rounds 3]   one request every gap ms (editor pattern)
  met_bench.py init  [--rounds 25]   spawn -> initialize ms + phys_footprint, warm image (one run per variant per round)
  met_bench.py idle  [--secs 30]     threads, CPU, footprint at idle
  met_bench.py query [--rounds 3]    footprint settled on lib, after 600 queries, 3 s later
  met_bench.py start [--rounds 40] [--obs]   spawn -> initialize reply and spawn -> first diagnostics ms (lib copy, bg
                                     pass running), warm image, one spawn per variant per round, order shuffled,
                                     runs back to back (--pause-ms 50 lets the CPU idle: 31 -> 40 ms, wide)
  met_bench.py pipe  [--rounds 8] [--runs 2]   lib background pass (initialized -> progress end), didChange ->
                                     diagnostics on the 88 KB file, spawn -> first diagnostics (lsp_bench.py pipe)
--base = nx-lsp of the base tree, --mova-bin = Mova with the native tap (this tree's variants), --scratch = dir holding
one private XDG_CACHE_HOME per variant (xdg-base/base2/off/on/obs). --obs adds the variant on + journal (NX_OBS=1).
MOVA_TRANSPORT_SPIN and friends pass through the env."""
import argparse, os, random, re, shutil, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "lsp_bench.py")
HERE_LSP = os.path.join(HERE, "..", "bin", "nx-lsp")
NAMES = ("base", "base2", "off", "on")


def variants(a):
    extra = [(x.split("=", 1)[0], x.split("=", 1)[1], "1") for x in a.extra]   # diagnostic builds, run with NX_METRICS=1
    obs = [("obs", HERE_LSP, "obs")] if a.obs else []
    return [("base", a.base, None), ("base2", a.base, None), ("off", HERE_LSP, None), ("on", HERE_LSP, "1")] + obs + extra


def env_of(a, v):
    """Environment of one variant: its own cache, a fresh state dir, an empty config dir."""
    name, _, met = v
    env = {k: x for k, x in os.environ.items() if k not in ("NX_METRICS", "NX_OBS", "MOVA_BIN")}
    if a.mova_bin and not name.startswith("base"):           # this tree needs the Mova with the native tap
        env["MOVA_BIN"] = a.mova_bin
    env["XDG_CACHE_HOME"] = os.path.join(a.scratch, "xdg-" + name)
    state = os.path.join(a.scratch, "state-" + name)         # fresh per run: lsp_bench --met reads the one session file
    shutil.rmtree(state, ignore_errors=True)
    env.update(XDG_STATE_HOME=state, XDG_CONFIG_HOME=os.path.join(a.scratch, "config-empty"), NX_TEST_XDG="1")
    if met:
        env["NX_METRICS"] = "1"
    if met == "obs":
        env["NX_OBS"] = "1"
    return env


def bench(a, v, args):
    name, server, met = v
    env = env_of(a, v)
    if met and args[0] in ("hop", "sparse") and name in ("on", "obs"):
        args = args + ["--met"]
    p = subprocess.run([sys.executable, BENCH] + args + ["--server", server], env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=900)
    return p.stdout


def num(pat, text):
    m = re.search(pat, text)
    return float(m.group(1)) if m else None


def stat(xs, unit, scale=1.0):
    xs = [x * scale for x in xs if x is not None]
    if not xs:
        return "n/a"
    return f"median {statistics.median(xs):.2f} min {min(xs):.2f} max {max(xs):.2f} {unit} (runs {len(xs)})"


def rounds(a, args, pats):
    """Runs every variant once per round, order rotated. Returns {variant: {key: [values]}} and the raw outputs."""
    vs = variants(a)
    res = {v[0]: {k: [] for k in pats} for v in vs}
    raw = {v[0]: [] for v in vs}
    loads = []
    for r in range(a.rounds):
        loads.append(os.getloadavg()[0])
        for v in vs[r % len(vs):] + vs[:r % len(vs)]:
            out = bench(a, v, args)
            raw[v[0]].append(out)
            for k, pat in pats.items():
                res[v[0]][k].append(num(pat, out))
    print(f"# rounds={a.rounds} load median {statistics.median(loads):.1f} min {min(loads):.1f} max {max(loads):.1f}")
    return res, raw


def med(res, v, k):
    xs = [x for x in res[v][k] if x is not None]
    return statistics.median(xs) if xs else float("nan")


def cmd_hop(a):
    pats = {"p50": r"ping-pong .*p50=([\d.]+)", "p99": r"ping-pong .*p99=([\d.]+)",
            "pipe_p50": r"pipelined .*latency ms p50=([\d.]+)", "rps": r"throughput (\d+) req/s"}
    res, raw = rounds(a, ["hop", "--n", str(a.n)] + (["--only-pingpong"] if a.only_pingpong else []), pats)
    for v in NAMES:
        print(f"{v:4} ping-pong p50 {stat(res[v]['p50'], 'us', 1000)} | p99 {stat(res[v]['p99'], 'us', 1000)}")
    for v in NAMES:
        print(f"{v:4} pipelined {stat(res[v]['rps'], 'req/s')} | latency p50 {stat(res[v]['pipe_p50'], 'us', 1000)}")
    for v in NAMES:                                          # every run, sorted: shows two modes, if any
        print(f"{v:6} p50 runs us: " + " ".join(f"{x * 1000:.0f}" for x in sorted(x for x in res[v]["p50"] if x is not None)))
    for v in NAMES:
        print(f"{v:6} p99 runs us: " + " ".join(f"{x * 1000:.0f}" for x in sorted(x for x in res[v]["p99"] if x is not None)))
    d = lambda x, y, k: (med(res, x, k) - med(res, y, k)) * 1000
    for x, y in [("base2", "base"), ("off", "base"), ("on", "base"), ("on", "off")] + [(n, "base") for n in NAMES[4:]]:
        print(f"delta of medians {x}-{y}: p50 {d(x, y, 'p50'):+.2f} us, p99 {d(x, y, 'p99'):+.2f} us" + ("   <- A/A noise" if x == "base2" else ""))
    for out in raw["on"]:
        print("  " + " ; ".join(l for l in out.splitlines() if l.startswith("met: sent")))


def cmd_sparse(a):
    pats = {"p50": r"sparse .*p50=([\d.]+)", "p90": r"sparse .*p90=([\d.]+)", "p99": r"sparse .*p99=([\d.]+)"}
    res, raw = rounds(a, ["sparse", "--n", str(a.n), "--gap-ms", str(a.gap_ms)], pats)
    for v in NAMES:
        print(f"{v:5} sparse gap {a.gap_ms} ms: p50 {stat(res[v]['p50'], 'us', 1000)} | p90 {stat(res[v]['p90'], 'us', 1000)} | p99 {stat(res[v]['p99'], 'us', 1000)}")
    d = lambda x, y, k: (med(res, x, k) - med(res, y, k)) * 1000
    for x in NAMES[1:]:
        print(f"delta of medians {x}-base: p50 {d(x, 'base', 'p50'):+.2f} us, p90 {d(x, 'base', 'p90'):+.2f} us, p99 {d(x, 'base', 'p99'):+.2f} us" + ("   <- A/A noise" if x == "base2" else ""))
    for out in raw["on"]:
        print("  " + " ; ".join(l for l in out.splitlines() if l.startswith("met: sent")))


def cmd_init(a):
    pats = {"ms": r"init warm: .*ms p50=([\d.]+)", "mb": r"init warm: .*footprint MB p50=([\d.]+)"}
    res, _ = rounds(a, ["init", "--runs", "1"], pats)
    for v in NAMES:
        print(f"{v:4} init warm {stat(res[v]['ms'], 'ms')} | footprint {stat(res[v]['mb'], 'MB')}")
    for x in NAMES[1:]:
        print(f"delta of medians {x}-base: {med(res, x, 'ms') - med(res, 'base', 'ms'):+.2f} ms, {med(res, x, 'mb') - med(res, 'base', 'mb'):+.2f} MB")


def cmd_idle(a):
    for v in variants(a):
        print(f"{v[0]:4} {bench(a, v, ['idle', '--secs', str(a.secs)]).strip()}")


def cmd_query(a):
    pats = {"settle": r"footprint after settle\+open ([\d.]+) MB", "after": r"footprint after queries ([\d.]+) MB", "later": r"footprint 3 s later ([\d.]+) MB",
            "threads": r"threads (\d+)"}
    res, _ = rounds(a, ["query", "--n", "100", "--lib", a.lib], pats)
    for v in NAMES:
        print(f"{v:4} settled+open {stat(res[v]['settle'], 'MB')} | after queries {stat(res[v]['after'], 'MB')} | 3 s later {stat(res[v]['later'], 'MB')} | threads {stat(res[v]['threads'], '')}")
    for x in ("base2", "off", "on"):
        print(f"delta of medians {x}-base MB: settled {med(res, x, 'settle') - med(res, 'base', 'settle'):+.2f}, after queries {med(res, x, 'after') - med(res, 'base', 'after'):+.2f}, 3 s later {med(res, x, 'later') - med(res, 'base', 'later'):+.2f}")


def cmd_pipe(a):
    pats = {"bg": r"bg pass: initialized->progress end ms p50=([\d.]+)", "chg": r"didChange->diagnostics .* ms p50=([\d.]+)",
            "first": r"warm: .*first diagnostics .* ms p50=([\d.]+)", "peak": r"footprint MB peak\(sampled\) p50=([\d.]+)"}
    res, _ = rounds(a, ["pipe", "--runs", str(a.runs), "--lib", a.lib], pats)
    names = {"bg": "bg pass ms", "chg": "didChange->diagnostics ms", "first": "spawn->first diagnostics ms", "peak": "pass peak MB"}
    for k in pats:
        for v in NAMES:
            print(f"{v:5} {names[k]}: {stat(res[v][k], '')}")
    for x, y in (("base2", "base"), ("off", "base"), ("on", "base"), ("on", "off")):
        print(f"delta of medians {x}-{y}: " + ", ".join(f"{names[k]} {med(res, x, k) - med(res, y, k):+.2f}" for k in pats) + ("   <- A/A noise" if x == "base2" else ""))


def cmd_start(a):
    sys.path.insert(0, HERE)
    os.environ["NX_TEST_XDG"] = "1"
    import lsp_bench as lb
    root = lb.fresh_copy(os.path.abspath(a.lib))
    target = os.path.join(root, "src/clojure_lsp/handlers.clj")
    text = open(target).read()
    vs = variants(a)
    keep = dict(os.environ)

    def direct(v, env):
        """The launcher's work done here (env + argv), so a run has no bash in it: less spawn noise."""
        nx = os.path.dirname(os.path.dirname(os.path.abspath(v[1])))
        img = "nx-met" if (env.get("NX_METRICS") == "1" and not v[0].startswith("base")) else "nx"
        env.setdefault("MOVA_IMAGE", os.path.join(env["XDG_CACHE_HOME"], "nx", img + ".img"))
        env.setdefault("MOVA_IMAGE_PRELOAD", "nx.main-met" if img == "nx-met" else "nx.main")
        env.update(MOVA_REFLECTION_WARNINGS="0", MOVA_SHARDS=env.get("MOVA_SHARDS", "2"), MOVA_JIT=env.get("MOVA_JIT", "0"))
        return [env.get("MOVA_BIN", "mova"), "--module-path", nx + "/src", nx + "/bin/nx-entry.mova"]

    def once(v):
        env = env_of(a, v)
        if "=" in v[1]:                                      # --direct extra: on + these variables
            env.update(x.split("=", 1) for x in v[1].split(","))
            v = (v[0], HERE_LSP, v[2])
        cmd = direct(v, env) if a.direct else v[1]
        os.environ.clear(); os.environ.update(env)
        s = lb.Server(cmd)
        t, _ = s.initialize(root)
        s.send(lb.open_msg("file://" + target, text))
        while True:
            t1, m = s.recv()
            if lb.is_pub(m):
                break
        time.sleep(a.settle_ms / 1000.0)                     # lets the pass end: no half-written caches for the next run
        s.close()
        os.environ.clear(); os.environ.update(keep)
        return t, (t1 - s.t0) * 1000

    for v in vs:                                             # images and jar caches warm
        once(v); once(v)
    res = {v[0]: ([], []) for v in vs}
    loads = []
    rnd = random.Random(7)                                   # shuffled, not rotated: a run's time depends on what ran before it
    for r in range(a.rounds):
        loads.append(os.getloadavg()[0])
        for v in rnd.sample(vs, len(vs)):
            time.sleep(a.pause_ms / 1000.0)
            i, f = once(v)
            res[v[0]][0].append(i); res[v[0]][1].append(f)
    print(f"# start: rounds={a.rounds} load median {statistics.median(loads):.1f} min {min(loads):.1f} max {max(loads):.1f}")
    md = statistics.median
    if a.dump:
        import json
        json.dump(res, open(a.dump, "w"))
    for k, nm in ((0, "spawn->initialize reply"), (1, "spawn->first diagnostics")):
        for v in vs:
            xs = res[v[0]][k]
            print(f"{v[0]:5} {nm} ms: median {md(xs):.2f} min {min(xs):.2f} p25 {sorted(xs)[len(xs) // 4]:.2f} max {max(xs):.2f}")
        for v in vs[1:]:
            xs, b = res[v[0]][k], res["base"][k]
            print(f"delta {v[0]}-base {nm}: median {md(xs) - md(b):+.2f} ms, min {min(xs) - min(b):+.2f} ms, p25 {sorted(xs)[len(xs) // 4] - sorted(b)[len(b) // 4]:+.2f} ms" + ("   <- A/A noise" if v[0] == "base2" else ""))
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["hop", "sparse", "init", "idle", "query", "pipe", "start"])
    ap.add_argument("--base", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--mova-bin", default=None, help="Mova binary for this tree's variants (base keeps its default)")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--extra", action="append", default=[], metavar="NAME=NX_LSP", help="one more variant (metrics on), for diagnosis")
    ap.add_argument("--runs", type=int, default=2, help="pipe: runs per phase inside one round")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--gap-ms", type=float, default=50)
    ap.add_argument("--only-pingpong", action="store_true")
    ap.add_argument("--obs", action="store_true", help="also run the variant on + journal (NX_OBS=1)")
    ap.add_argument("--pause-ms", type=float, default=0, help="start: sleep before every run")
    ap.add_argument("--direct", action="store_true", help="start: spawn Mova without the bash launcher (extras: NAME=KEY=VAL,KEY=VAL on top of on)")
    ap.add_argument("--dump", default=None, help="start: write every sample to this JSON file")
    ap.add_argument("--settle-ms", type=float, default=150, help="start: wait after the first diagnostics before shutdown")
    ap.add_argument("--lib", default=os.path.join(HERE, "..", "..", "lib"))
    a = ap.parse_args()
    NAMES += (("obs",) if a.obs else ()) + tuple(x.split("=", 1)[0] for x in a.extra)
    {"hop": cmd_hop, "sparse": cmd_sparse, "init": cmd_init, "idle": cmd_idle, "query": cmd_query, "pipe": cmd_pipe, "start": cmd_start}[a.cmd](a)
