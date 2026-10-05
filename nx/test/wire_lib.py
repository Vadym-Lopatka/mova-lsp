#!/usr/bin/env python3
"""Raw wire comparison nx vs oracle on a real project (default lib/): sampled token positions of a few files,
definition/hover/references/completion. Usage: XDG_CACHE_HOME=<tmp> python3 nx/test/wire_lib.py [--project DIR] [--n 120] [--show 6]"""
import argparse, importlib.util, json, os, shutil, sys, tempfile, time, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import nxenv  # private XDG_STATE_HOME / XDG_CONFIG_HOME for every spawned server
HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.abspath(os.path.join(HERE, "..", ".."))
spec = importlib.util.spec_from_file_location("lsp_e2e_run", os.path.join(WT, "mova/lsp-e2e/run.py"))
sys.path.insert(0, os.path.join(WT, "mova/lsp-e2e"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
FILES = ["src/clojure_lsp/queries.clj", "src/clojure_lsp/shared.clj", "src/clojure_lsp/feature/hover.clj", "src/clojure_lsp/kondo.clj", "src/clojure_lsp/dep_graph.clj"]


def probes_for(root, files, n):
    pr = []
    per = max(1, n // len(files))
    for rel in files:
        text = (root / rel).read_text()
        starts = [0] + [m.end() for m in __import__("re").finditer("\n", text)]
        toks = list(run.tokenize(text))
        step = max(1, len(toks) // per)
        seen = set()
        for off, tok in toks[::step][:per]:
            line = max(k for k, s in enumerate(starts) if s <= off)
            pos = {"line": line, "character": off - starts[line]}
            td = {"uri": (root / rel).as_uri()}
            key = f"{rel}:{pos['line']}:{pos['character']}"
            base = {"textDocument": td, "position": pos}
            pr.append((f"{key}:definition", "textDocument/definition", base))
            pr.append((f"{key}:hover", "textDocument/hover", base))
            pr.append((f"{key}:references", "textDocument/references", {**base, "context": {"includeDeclaration": True}}))
            if len(tok) >= 2 and not tok.startswith(":"):
                pr.append((f"{key}:completion", "textDocument/completion", {"textDocument": td, "position": {"line": line, "character": off - starts[line] + 2}}))
    return pr


def session(cmd, env, root, files, probes, wait=90, extra=3):
    c = run.Client(cmd, env, None, open(os.devnull, "wb"))
    c.request("initialize", {"processId": os.getpid(), "rootUri": root.as_uri(), "capabilities": {"window": {"workDoneProgress": True}},
                             "initializationOptions": run.INIT_OPTIONS, "workDoneToken": "e2e-init"}, 60)
    c.notify("initialized", {})
    for rel in files:
        c.notify("textDocument/didOpen", {"textDocument": {"uri": (root / rel).as_uri(), "languageId": "clojure", "version": 1, "text": (root / rel).read_text()}})
    c.done.wait(wait)
    time.sleep(extra)
    res = []
    for _, m, p in probes:  # one at a time: the oracle drops answers under a burst on a big project
        try:
            res.append(c.request(m, p, 60))
        except Exception as e:
            res.append({"__error__": str(e)})
    c.p.kill()
    return {k: r for (k, m, _), r in zip(probes, res)}


def canon(m, v):
    if m == "references" and isinstance(v, list):
        return sorted(v, key=lambda x: json.dumps(x, sort_keys=True))
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.path.join(WT, "lib"))
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--show", type=int, default=6)
    ap.add_argument("--oracle", default=os.path.join(WT, "mova/bin/clojure-lsp"))
    ap.add_argument("--nx", default=os.path.join(WT, "nx/bin/nx-lsp"))
    a = ap.parse_args()
    xdg = run.xdg_dir()
    out = {}
    roots = {}
    for name, cmd, mb in (("jvm", a.oracle, run.DEFAULT_MOVA), ("nx", a.nx, os.environ.get("MOVA_BIN", ""))):
        d = pathlib.Path(tempfile.mkdtemp(prefix="nxwlib")).resolve()
        shutil.copytree(a.project, d / "lib", ignore=shutil.ignore_patterns(".lsp", ".cpcache", "target"))
        root = d / "lib"
        roots[name] = root
        env = run.make_env(argparse.Namespace(mova_bin=mb), xdg)
        if name == "jvm":
            env = run.jdk_pinned_env(env) if hasattr(run, "jdk_pinned_env") else env
        pr = probes_for(root, FILES, a.n)
        t = time.time()
        out[name] = (session(cmd.split(), env, root, FILES, pr, extra=(8 if name == "jvm" else 1)), pr)
        print(f"{name}: {len(pr)} probes in {time.time() - t:.1f}s", file=sys.stderr)
    (jr, pr), (nr, _) = out["jvm"], out["nx"]
    stats = {}
    def fix(v, r):
        t = json.dumps(v)
        for p in {os.path.realpath(str(r)), str(r)}:
            t = t.replace("file://" + p, "<root>").replace(p, "<root>")
        return json.loads(t)
    import collections
    kinds = collections.Counter(type(v).__name__ + (':err' if isinstance(v, dict) and '__error__' in v else '') for k, v in jr.items() if k.endswith('references'))
    print('jvm references result types:', dict(kinds), file=sys.stderr)
    for key, meth, _ in pr:
        m = meth.split("/")[1]
        x, y = canon(m, fix(jr[key], roots["jvm"])), canon(m, fix(nr[key], roots["nx"]))
        eq = x == y or (x in (None, [], {}) and y in (None, [], {}))
        st = stats.setdefault(m, [0, 0, []])
        st[0 if eq else 1] += 1
        if not eq and len(st[2]) < a.show:
            if m == "references" and isinstance(x, list) and isinstance(y, list):
                sx, sy = {json.dumps(i, sort_keys=True) for i in x}, {json.dumps(i, sort_keys=True) for i in y}
                st[2].append((key, "only jvm: " + "; ".join(sorted(sx - sy))[:500], "only nx: " + "; ".join(sorted(sy - sx))[:500]))
            elif m == "completion" and isinstance(x, list) and isinstance(y, list):
                ident = lambda i: json.dumps({k: v for k, v in i.items() if k != "textEdit"}, sort_keys=True)
                sx, sy = {ident(i) for i in x}, {ident(i) for i in y}
                st[2].append((key, "only jvm: " + "; ".join(sorted(sx - sy))[:600], "only nx: " + "; ".join(sorted(sy - sx))[:600]))
            else:
                st[2].append((key, json.dumps(x)[:500], json.dumps(y)[:500]))
    for m, (e, d, ex) in stats.items():
        print(f"{m:12} equal {e:3} differ {d:3}")
        for k, x, y in ex:
            print(f"   {k}\n     jvm {x}\n     nx  {y}")


if __name__ == "__main__":
    main()
