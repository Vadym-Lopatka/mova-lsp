#!/usr/bin/env python3
"""Raw wire comparison of nx vs the JVM-answer oracle using the lsp-e2e machinery (same project copies, same settle logic).
Captures the raw result of every settled probe for both servers and diffs them as JSON (root-normalised).
Usage: XDG_CACHE_HOME=<tmp> python3 nx/test/wire_diff.py [--only clj] [--show 4] [--oracle CMD] [--nx CMD]"""
import argparse, importlib.util, json, os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import nxenv  # private XDG_STATE_HOME / XDG_CONFIG_HOME for every spawned server
HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.abspath(os.path.join(HERE, "..", ".."))
spec = importlib.util.spec_from_file_location("lsp_e2e_run", os.path.join(WT, "mova/lsp-e2e/run.py"))
sys.path.insert(0, os.path.join(WT, "mova/lsp-e2e"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

CAP = {}
_orig = run.run_batch


def run_batch(c, probes, norm, raw=None, timeout=20):
    raw = [] if raw is None else raw
    out = _orig(c, probes, norm, raw, timeout)
    if len(raw) > 5 and "early" not in CAP.get("phase", ""):
        CAP.setdefault(tuple(sorted(norm.files)), []).append((norm.root, list(raw)))
    return out


run.run_batch = run_batch


def collect(cmd, only, mova_bin):
    CAP.clear()
    xdg = run.xdg_dir()
    xdg.mkdir(parents=True, exist_ok=True)
    errdir = __import__("pathlib").Path(tempfile.mkdtemp(prefix="nx-wire-logs-"))
    names = [d.name for d in sorted((run.HERE / "projects").iterdir()) if (d / "e2e.json").exists() and d.name != "mova" and (not only or d.name == only)]
    args = argparse.Namespace(server_cmd=cmd, mova_bin=mova_bin, full=False, settled_only=True, only=only, store_warm=False, budget=10, json=False)
    tmps = []
    try:
        run.collect(args, names, xdg, tmps, errdir)
    finally:
        for d in tmps:
            shutil.rmtree(d, ignore_errors=True)
    out = {}
    for files, lst in CAP.items():
        root, raw = lst[-1]
        out[files] = (root, {k: r for k, m, r in raw}, {k: m for k, m, r in raw})
    return out


def canon(method, v):
    if method == "references" and isinstance(v, list):
        return sorted(v, key=lambda x: json.dumps(x, sort_keys=True))
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--show", type=int, default=4)
    ap.add_argument("--oracle", default=os.path.join(WT, "mova/bin/clojure-lsp"))
    ap.add_argument("--nx", default=os.path.join(WT, "nx/bin/nx-lsp"))
    ap.add_argument("--methods", default="definition,hover,references,completion")
    a = ap.parse_args()
    nxbin = os.environ.get("MOVA_BIN", "")
    jvm = collect(a.oracle, a.only, run.DEFAULT_MOVA)
    nx = collect(a.nx, a.only, nxbin)
    want = set(a.methods.split(","))
    for files, (jroot, jraw, meths) in jvm.items():
        if files not in nx:
            print("no nx capture for", files)
            continue
        nroot, nraw, _ = nx[files]
        def fix(v, r):
            t = json.dumps(v)
            for p in {os.path.realpath(str(r)), str(r)}:
                t = t.replace("file://" + p, "<root>").replace(p, "<root>")
            return json.loads(t)
        stats = {}
        print("==", files)
        for k, jr in jraw.items():
            m = meths[k].split("/")[1]
            if m not in want:
                continue
            x, y = canon(m, fix(jr, jroot)), canon(m, fix(nraw.get(k), nroot))
            eq = x == y or (x in (None, [], {}) and y in (None, [], {}))
            st = stats.setdefault(m, [0, 0, []])
            st[0 if eq else 1] += 1
            if not eq and len(st[2]) < a.show:
                if m == "references" and isinstance(x, list) and isinstance(y, list):
                    sx, sy = {json.dumps(i, sort_keys=True) for i in x}, {json.dumps(i, sort_keys=True) for i in y}
                    st[2].append((k, "only jvm: " + "; ".join(sorted(sx - sy))[:700], "only nx: " + "; ".join(sorted(sy - sx))[:700]))
                else:
                    st[2].append((k, json.dumps(x)[:600], json.dumps(y)[:600]))
        for m, (e, d, ex) in stats.items():
            print(f"{m:12} equal {e:3} differ {d:3}")
            for k, x, y in ex:
                print(f"   {k}\n     jvm {x}\n     nx  {y}")


if __name__ == "__main__":
    main()
