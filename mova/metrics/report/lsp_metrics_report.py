#!/usr/bin/env python3
"""lsp-metrics-report: machine-first report over metrics JSONL (stdlib only)."""
import argparse, glob, json, math, os, sys, datetime
from collections import Counter, defaultdict

MB = 1048576.0
BUCKETS = [(4096, "<4K"), (16384, "4-16K"), (65536, "16-64K"), (262144, "64-256K")]


def pct(xs, p):  # nearest-rank percentile of a sorted list
    return xs[max(0, math.ceil(p / 100.0 * len(xs)) - 1)]


def stats(xs, nd=1):  # n, p50, p90, p99, max
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p50": round(pct(xs, 50), nd), "p90": round(pct(xs, 90), nd),
            "p99": round(pct(xs, 99), nd), "max": round(xs[-1], nd)}


def ms(ns):
    return round(ns / 1e6, 1)


def size_bucket(b):
    if b is None:
        return "?"
    for lim, lab in BUCKETS:
        if b < lim:
            return lab
    return ">256K"


def run_bucket(n):
    return "1" if n == 1 else "2-5" if n <= 5 else "6+"


def load(paths, since, session, counts):  # stream lines into {session: [events]}
    sess = defaultdict(list)
    for p in paths:
        with open(p, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    e["session"], e["t_ns"], e["name"]
                except Exception:
                    counts["bad_lines"] += 1
                    continue
                if not isinstance(e.get("attrs"), dict):
                    e["attrs"] = {}
                if since is not None and e.get("ts_ms", 0) < since:
                    continue
                if session and e["session"] != session:
                    continue
                counts["events"] += 1
                sess[e["session"]].append(e)
    for evs in sess.values():
        evs.sort(key=lambda e: e["t_ns"])
    return sess


def sinfo(evs):  # session facts from start/end events
    s = next((e["attrs"] for e in evs if e["name"] == "session.start"), {})
    en = next((e["attrs"] for e in evs if e["name"] == "session.end"), {})
    ms = next((e["attrs"] for e in evs if e["name"] == "mova.startup"), {})  # launcher sets MOVA_JIT after the tap
    jit = ms.get("jit.enabled", s.get("env.MOVA_JIT", "?"))
    build = "%s+%s+jit%s" % (s.get("build.mova_rev", "?"), s.get("build.lsp_rev", "?"), jit)
    up = en.get("uptime_ns", evs[-1]["t_ns"] if evs else 0)  # no session.end: last event's t_ns
    return {"kind": s.get("server.kind", "unknown"), "build": build, "uptime_ns": up, "end": en, "start": s,
            "end_reason": en.get("end.reason", "unknown" if en else "none"), "has_end": bool(en)}


def sec_sessions(S, info):
    ups = sorted(ms(i["uptime_ns"]) for i in info.values())
    return {"count": len(S), "by_kind": dict(Counter(i["kind"] for i in info.values())),
            "by_build": dict(Counter(i["build"] for i in info.values())),
            "uptime_ms_p50": pct(ups, 50) if ups else None, "uptime_ms_max": ups[-1] if ups else None,
            "by_end_reason": dict(Counter(i["end_reason"] for i in info.values())),
            "no_session_end": sum(1 for i in info.values() if not i["has_end"]),
            "uptime_note": "sessions without session.end use their last event's t_ns as uptime"}


def sec_latency(S, info):
    g = defaultdict(list)
    for evs in S.values():
        for e in evs:
            a = e["attrs"]
            if e["name"] in ("lsp.request", "lsp.initialize", "lsp.diagnostics") and "dur_ns" in e:
                k = (e["name"], a.get("method") or a.get("trigger") or "", a.get("file.ext", "?"), size_bucket(a.get("file.bytes")))
                g[k].append(e["dur_ns"] / 1e6)
    rows = [dict(zip(("name", "method", "ext", "size"), k), **stats(v)) for k, v in g.items()]
    return sorted(rows, key=lambda r: -r["p90"])


def diag_open(evs):
    return next((e for e in evs if e["name"] == "lsp.diagnostics" and e["attrs"].get("trigger") == "didOpen"), None)


def sec_open_file(S, info):
    rows = []
    for sid, evs in S.items():
        d = diag_open(evs)
        if not d:
            continue
        t1, t0 = d["t_ns"], d["t_ns"] - d.get("dur_ns", 0)
        r = {"session": sid, "kind": info[sid]["kind"], "ms": ms(d.get("dur_ns", 0)), "bytes": d["attrs"].get("file.bytes")}
        win = [e for e in evs if e["name"].startswith("srv.") and e.get("dur_ns") is not None and t0 <= e["t_ns"] <= t1 + 1e6 and e["t_ns"] - e["dur_ns"] >= t0 - 1e6]
        has_srv = any(e["name"].startswith(("srv.", "mova.")) for e in evs)
        st = sum(e.get("dur_ns", 0) for e in evs if e["name"] == "srv.startup")
        ms_ev = [e["attrs"] for e in evs if e["name"] == "mova.startup"]
        st += sum(a.get("image.restore_ns", 0) + a.get("preload_ns", 0) for a in ms_ev)
        if has_srv:
            an = sum(e["dur_ns"] for e in win if e["name"] == "srv.analyze")
            dg = [e["attrs"] for e in win if e["name"] == "srv.diagnostics"]
            rest = sum(a.get(k, 0) for a in dg for k in ("normalize_ns", "linters_ns", "publish_ns"))
            kf = [e["attrs"] for e in win if e["name"] == "srv.kondo.file"]
            r.update({"startup_ms": ms(st), "analyze_ms": ms(an), "diag_normalize_ms": ms(sum(a.get("normalize_ns", 0) for a in dg)),
                      "diag_linters_ms": ms(sum(a.get("linters_ns", 0) for a in dg)), "diag_publish_ms": ms(sum(a.get("publish_ns", 0) for a in dg)),
                      "kondo_parse_ms": ms(sum(a.get("parse_ns", 0) for a in kf)), "kondo_analyze_ms": ms(sum(a.get("analyze_ns", 0) for a in kf)),
                      "kondo_lint_ms": ms(sum(e["attrs"].get("kondo.lint_ns", 0) for e in win if e["name"] == "srv.analyze"))})  # end lints run once per kondo run
            r["unaccounted_ms"] = round(r["ms"] - ms(an + rest), 1)  # analyze contains kondo; add post-kondo diag phases
        rows.append(r)
    return rows


REQS = ("completion", "hover", "references", "definition")


def sec_typing(S, info, by_build):
    g = defaultdict(lambda: defaultdict(list))
    for sid, evs in S.items():
        grp = info[sid]["build"] if by_build else info[sid]["kind"]
        for e in evs:
            if "dur_ns" not in e:
                continue
            a = e["attrs"]
            if e["name"] == "lsp.diagnostics" and a.get("trigger") == "didChange":
                g[grp]["diagnostics.didChange"].append(e["dur_ns"] / 1e6)
            elif e["name"] == "lsp.request" and str(a.get("method", "")).rsplit("/", 1)[-1] in REQS:
                g[grp][a["method"].rsplit("/", 1)[-1]].append(e["dur_ns"] / 1e6)
    rows = []
    for m in ("diagnostics.didChange",) + REQS:
        for grp in sorted(g):
            if m in g[grp]:
                rows.append(dict({"metric": m, "group": grp}, **stats(g[grp][m])))
    return rows


def sec_kondo(S, info):
    b = defaultdict(lambda: {"parse": [], "analyze": [], "unjoined": 0})
    lint = an_ns = 0
    for evs in S.values():
        ans = [e for e in evs if e["name"] == "srv.analyze" and "dur_ns" in e]
        for e in evs:
            if e["name"] != "srv.kondo.file":
                continue
            a = e["attrs"]
            k1 = e["t_ns"]
            k0 = k1 - e.get("dur_ns", 0)
            an = next((x for x in ans if x["t_ns"] - x["dur_ns"] <= k0 + 1000 and k1 <= x["t_ns"] + 1000), None)
            n = an["attrs"].get("kondo.run_n") if an else None
            if n is None:
                b["?"]["unjoined"] += 1
                continue
            by = a.get("file.bytes") or (an["attrs"].get("bytes") if an["attrs"].get("files") == 1 else None)
            if by:
                bk = b[run_bucket(n)]
                bk["parse"].append(a.get("parse_ns", 0) / 1000.0 / by)
                bk["analyze"].append(a.get("analyze_ns", 0) / 1000.0 / by)
        an_ns += sum(x["dur_ns"] for x in ans)
        lint += sum(x["attrs"].get("kondo.lint_ns", 0) for x in ans)
    rows = []
    for k in ("1", "2-5", "6+", "?"):
        if k in b:
            v = b[k]
            rows.append({"run_n": k, "unjoined": v["unjoined"], "parse_us_per_byte": stats(v["parse"], 3), "analyze_us_per_byte": stats(v["analyze"], 3)})
    return {"by_run_n": rows, "lint_share_of_analyze": round(lint / an_ns, 3) if an_ns else None}


def lsq_slope(xs, ys):  # least squares slope, None if x has no spread
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


def sec_memory(S, info):
    rows = []
    for sid, evs in S.items():
        sm = [e for e in evs if e["name"] == "proc.sample"]
        if not sm:
            continue
        def at(t):  # nearest sample at or after t
            s = next((s for s in sm if s["t_ns"] >= t), None)
            return s["attrs"] if s else None
        def fp(a, k="footprint"):
            return round(a[k] / MB, 1) if a and k in a else None
        ini = next((e for e in evs if e["name"] == "lsp.initialize"), None)
        d = diag_open(evs)
        a_i, a_o, a_e = (at(ini["t_ns"]) if ini else None), (at(d["t_ns"]) if d else None), sm[-1]["attrs"]
        chg = sorted(e["t_ns"] for e in evs if e["name"] == "lsp.notify" and str(e["attrs"].get("method", "")).endswith("didChange"))
        xs, j = [], 0
        for s in sm:
            while j < len(chg) and chg[j] <= s["t_ns"]:
                j += 1
            xs.append(j)
        sl = lsq_slope(xs, [s["attrs"].get("footprint", 0) / MB for s in sm])
        cen = next((e["attrs"] for e in evs if e["name"] == "project.census"), {})
        rows.append({"session": sid, "kind": info[sid]["kind"], "init_mb": fp(a_i), "init_rss_mb": fp(a_i, "rss"),
                     "open_mb": fp(a_o), "open_rss_mb": fp(a_o, "rss"),
                     "peak_mb": round(max(s["attrs"].get("footprint_peak", 0) for s in sm) / MB, 1),
                     "end_mb": fp(a_e), "end_rss_mb": fp(a_e, "rss"), "samples": len(sm), "didChange": len(chg),
                     "slope_mb_per_100_didChange": round(sl * 100, 3) if sl is not None else None,
                     "census_files": sum(v for k, v in cen.items() if k.startswith("files.")) if cen else None,
                     "census_bytes": cen.get("bytes.source")})
    return rows


def sec_runtime(S, info):
    rows = []
    for sid, evs in S.items():
        r = [e["attrs"] for e in evs if e["name"] == "mova.runtime"]
        if not r:
            continue
        a = r[-1]
        def ratio(u, s):
            t = a.get(u, 0) + a.get(s, 0)
            return round(a.get(u, 0) / t, 4) if t else None
        rows.append({"session": sid, "end_reason": info[sid]["end_reason"], "uptime_ms": ms(info[sid]["uptime_ns"]), "assoc_unique_ratio": ratio("assoc.unique", "assoc.shared"), "conj_unique_ratio": ratio("conj.unique", "conj.shared"),
                     "jit_compiled": a.get("jit.compiled"), "jit_compile_ms": ms(a.get("jit.compile_ns", 0))})
    return rows


def sec_errors(S, info):
    lv, msgs, resync, dropped, bad = Counter(), defaultdict(Counter), 0, 0, 0
    codes = Counter()
    for sid, evs in S.items():
        for e in evs:
            a = e["attrs"]
            if e["name"] == "lsp.log":
                lv[a.get("level", "?")] += 1
                msgs[a.get("level", "?")][a.get("msg", "")] += 1
            elif e["name"] == "tap.resync":
                resync += 1
            elif e["name"] == "session.end":
                dropped += a.get("events.dropped", 0)
                bad += a.get("server.bad", 0)
            elif e["name"] in ("lsp.request", "lsp.initialize") and "error.code" in a:
                codes["%s %s" % (a.get("method", e["name"]), a["error.code"])] += 1
    return {"log_by_level": dict(lv), "top_messages": {k: [{"n": n, "msg": m} for m, n in c.most_common(5)] for k, c in msgs.items()},
            "tap_resync": resync, "events_dropped": dropped, "server_bad": bad, "request_errors": dict(codes)}


def sec_questions(S, info):
    def nm(*names, **cond):
        return lambda e, k: e["name"] in names and all(e["attrs"].get(a) == v for a, v in cond.items())
    Q = {1: [nm("lsp.diagnostics", trigger="didOpen"), nm("mova.startup", "srv.startup", "srv.analyze", "srv.diagnostics")],
         2: [nm("srv.kondo.file")], 3: [nm("mova.runtime")], 6: [nm("srv.kondo.file"), nm("srv.analyze")],
         7: [nm("lsp.diagnostics", trigger="didChange"), nm("lsp.request")],
         8: [nm("project.census", "srv.bg", "proc.sample")],
         9: [lambda e, k: k == "jvm" and e["name"] in ("lsp.request", "lsp.diagnostics")],
         10: [nm("proc.sample", "lsp.initialize")], 11: [nm("proc.sample", "project.census")],
         13: [nm("srv.bg", "mova.startup")], 14: [nm("proc.sample")],
         15: [lambda e, k: e["name"] == "lsp.notify" and str(e["attrs"].get("method", "")).endswith("didChange"), nm("proc.sample")], 16: [nm("proc.sample")],
         4: [], 5: [], 12: [], 17: []}
    out = []
    for q, ps in Q.items():
        n = sum(1 for sid, evs in S.items() for e in evs if any(p(e, info[sid]["kind"]) for p in ps))
        out.append({"q": q, "has_data": n > 0, "n_events": n})
    return out


def build_report(S, counts, by_build):
    info = {sid: sinfo(evs) for sid, evs in S.items()}
    return {"input": dict(counts), "sessions": sec_sessions(S, info), "latency": sec_latency(S, info),
            "open_file": sec_open_file(S, info), "typing": sec_typing(S, info, by_build), "kondo": sec_kondo(S, info),
            "memory": sec_memory(S, info), "runtime": sec_runtime(S, info), "errors": sec_errors(S, info),
            "questions": sec_questions(S, info)}


def cj(v):
    return json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else ("" if v is None else str(v))


def table(rows):  # aligned text table from list of dicts
    cols = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    cells = [[cj(r.get(c)) for c in cols] for r in rows]
    w = [max(len(c), *(len(x[i]) for x in cells)) for i, c in enumerate(cols)]
    fmt = lambda xs: "  ".join(x.ljust(w[i]) for i, x in enumerate(xs)).rstrip()
    return "\n".join([fmt(cols)] + [fmt(x) for x in cells])


def render_text(rep):
    out = []
    for k, v in rep.items():
        out.append("== %s ==" % k)
        if isinstance(v, list):
            out.append(table(v) if v else "(none)")
        elif isinstance(v, dict) and isinstance(v.get("by_run_n"), list):
            out.append(table(v["by_run_n"]) if v["by_run_n"] else "(none)")
            out.append("lint_share_of_analyze: %s" % cj(v.get("lint_share_of_analyze")))
        else:
            out += ["%s: %s" % (a, cj(b)) for a, b in v.items()]
        out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lsp_metrics_report")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir")
    ap.add_argument("--since")
    ap.add_argument("--session")
    ap.add_argument("--kind", choices=["mova", "jvm"])
    ap.add_argument("--format", choices=["json", "text"], default="json")
    ap.add_argument("--by-build", action="store_true")
    a = ap.parse_args(argv)
    d = a.dir or os.environ.get("LSP_METRICS_DIR") or os.path.expanduser("~/.local/state/mova-lsp-metrics")
    paths = a.files or sorted(glob.glob(os.path.join(d, "events-*.jsonl")))
    since = None
    if a.since:
        since = int(datetime.datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    counts = Counter({"files": len(paths), "bad_lines": 0, "events": 0})
    S = load(paths, since, a.session, counts)
    if a.kind:
        S = {s: e for s, e in S.items() if sinfo(e)["kind"] == a.kind}
    rep = build_report(S, counts, a.by_build)
    print(render_text(rep) if a.format == "text" else json.dumps(rep, indent=None, separators=(",", ":")))


if __name__ == "__main__":
    main()
