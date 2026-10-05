"""S1 dependency-file scenarios (DEPS-DESIGN §7). R = the run module (helpers)."""
import re, threading, time
from pathlib import Path
from urllib.parse import unquote


def stale_uri(uri):
    """Same jar uri with another version of the same artifact found next to it in ~/.m2, or None."""
    m = re.match(r"(?P<pre>[a-z]+:file:)(?P<path>.*/(?P<art>[^/]+)/(?P<ver>[^/]+)/(?P=art)-(?P=ver)\.jar)(?P<rest>!/.*)", uri)
    if not m:
        return None
    base = Path(unquote(m["path"])).parent.parent
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if d.name != m["ver"] and (d / f"{m['art']}-{d.name}.jar").exists():
            return m["pre"] + m["path"].replace(f"/{m['ver']}/{m['art']}-{m['ver']}.jar", f"/{d.name}/{m['art']}-{d.name}.jar") + m["rest"]
    return None


def write_copy(base, uri, text):
    """lsp-mode `lsp-clojure--file-in-jar`: <base>/workspace/.cache/<ns>.<ext> + sibling .<ns>.metadata holding the jar uri."""
    name = uri.split("!/", 1)[1].replace("/", ".")
    d = base / "workspace" / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    (d / f".{name.rsplit('.', 1)[0]}.metadata").write_text(uri)
    return d / name


def dep_probes(R, furi, text, n=10):
    """Definition/hover/references at n evenly spaced first occurrences of distinct symbols: [(key-suffix, method, params)]."""
    st, seen, toks = [0] + [m.end() for m in re.finditer("\n", text)], set(), []
    for off, tok in R.tokenize(text):
        if tok not in seen:
            seen.add(tok)
            toks.append(off)
    out = []
    for off in [toks[i * len(toks) // n] for i in range(n)] if len(toks) >= n else toks:
        ln = max(k for k, s0 in enumerate(st) if s0 <= off)
        pos = {"line": ln, "character": off - st[ln]}
        base = {"textDocument": {"uri": furi}, "position": pos}
        k = f"{ln}:{pos['character']}"
        out += [(f"{k}:definition", "textDocument/definition", base), (f"{k}:hover", "textDocument/hover", base),
                (f"{k}:references", "textDocument/references", {**base, "context": {"includeDeclaration": True}})]
    return out


def open_doc(c, furi, text, lang, cap):
    """didOpen, wait for the first publish, then 100 ms quiet. Returns (diags, open_ms)."""
    c.diag_ev[furi] = ev = threading.Event()
    t = time.monotonic()
    c.notify("textDocument/didOpen", {"textDocument": {"uri": furi, "languageId": lang, "version": 1, "text": text}})
    ev.wait(cap)
    ms = int((time.monotonic() - t) * 1000)
    while time.monotonic() - c.diag_at < 0.1:
        time.sleep(0.02)
    return c.diags.get(furi, []), ms


def run_dep(R, c, target, norm, lang, plan, dbase, cap):
    """S1 scenarios on one jar entry. plan: subset of open/stale/change. Returns ({key: value}, {open_ms, reopen_ms})."""
    uri = re.sub(r"^zipfile:(?:file:)?(//.*?)::", r"jar:file:\1!/", target[0])  # JVM dependencyContents wants lsp-mode's jar: form
    text = R.wait_all([c.send_batch([("clojure/dependencyContents", {"uri": uri})])[0]], 20)[0]
    if not isinstance(text, str) or not text:
        return {"depcopy:error": f"dependencyContents {uri} {str(text)[:60]}"}, {}
    got, ms = {}, {}

    def record(pfx, furi, diags, probes=True):
        got[f"depcopy:{pfx}:diagnostics"] = norm.diags(diags)
        if probes:
            res = R.run_batch(c, [(k, m, p) for k, m, p in dep_probes(R, furi, text)], norm)
            got.update({f"depcopy:{pfx}:{k}": v for k, v in res.items()})

    cap = max(cap, 10)
    path = write_copy(dbase / "main", uri, text)
    furi = path.as_uri()
    d, ms["open_ms"] = open_doc(c, furi, text, lang, cap)
    record("open", furi, d)
    c.diag_ev[furi] = ev = threading.Event()
    c.notify("textDocument/didClose", {"textDocument": {"uri": furi}})
    ev.wait(0.5)  # JVM publishes [] on close; it must not be taken for the reopen publish
    time.sleep(0.05)
    d, ms["reopen_ms"] = open_doc(c, furi, text, lang, cap)
    record("reopen", furi, d)
    if "change" in plan:
        c.diag_ev[furi] = ev = threading.Event()
        c.notify("textDocument/didChange", {"textDocument": {"uri": furi, "version": 2},
                                            "contentChanges": [{"text": text + "\n;; e2e change\n"}]})
        ev.wait(cap)
        while time.monotonic() - c.diag_at < 0.1:
            time.sleep(0.02)
        got["depcopy:change:diagnostics"] = norm.diags(c.diags.get(furi, []))
    c.notify("textDocument/didClose", {"textDocument": {"uri": furi}})
    other = stale_uri(uri) if "stale" in plan else None
    if "stale" in plan and not other:
        got["depcopy:stale:error"] = "no second version of the jar in ~/.m2"
    elif other:
        sp = write_copy(dbase / "stale", other, text)  # text of one version, metadata naming another
        d, _ = open_doc(c, sp.as_uri(), text, lang, cap)
        record("stale", sp.as_uri(), d)
        c.notify("textDocument/didClose", {"textDocument": {"uri": sp.as_uri()}})
    return got, ms




def run_all(R, c, jt, plan, norm, dbase, cap):
    """Pick the first clj/cljs jar definition (not the JDK) and run the scenarios on it."""
    tg = next((t for t in jt if "src.zip" not in t[0] and t[0].endswith((".clj", ".cljs", ".cljc"))), None)
    if not tg:
        return {"depcopy:error": "no clj/cljs jar definition found"}, {}
    return run_dep(R, c, tg, norm, "clojurescript" if tg[0].endswith(".cljs") else "clojure", plan, dbase, cap)
