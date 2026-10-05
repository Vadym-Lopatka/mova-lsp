"""lsp-e2e extended probes: every method nx must answer, vs JVM goldens (golden/<proj>.ext.json). Stdlib only.
Keys: '<rel>:<line>:<col>:<method>' (position probes), '<rel>:<method>' (file probes), 'ws:<query>:workspaceSymbol'.
The last ':'-separated part is the method name (nx-gate groups by it)."""
import json, os, re, time
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "ext-fixtures"
WS_QUERIES = ["", "app", "add", "greet", "u/", "run", "zzz-none"]
RENAME_TO = "renamed-x"
DEADLINE_S = 6.0  # shared deadline per batch


def messy_rel(name, files):
    """Runtime-created ext-only file (not on disk at startup: existing goldens stay unchanged)."""
    src = next(iter((FIXTURES / name).glob("messy.*")), None)
    if not src:
        return None, None
    return f"src/app/{src.name}", src


def sample(seq, n):
    seq = list(seq)
    if len(seq) <= n:
        return seq
    return [seq[i * len(seq) // n] for i in range(n)]


def apply_edits(text, edits):
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    def off(p):
        ln = min(p["line"], len(starts) - 1)
        return min(len(text), starts[ln] + p["character"])
    out = text
    for e in sorted(edits or [], key=lambda e: (off(e["range"]["start"]), off(e["range"]["end"])), reverse=True):
        out = out[:off(e["range"]["start"])] + e["newText"] + out[off(e["range"]["end"]):]
    return out


class Ext:
    def __init__(self, c, root, norm, files, legend, wait_all):
        self.c, self.root, self.norm, self.files, self.legend, self.wait_all = c, root, norm, files, legend, wait_all
        self.rs = str(root.resolve())
        self.text = {rel: (root / rel).read_text() for rel in files}

    # ---- normalizers ----
    def san(self, s):
        if not isinstance(s, str):
            return s
        s = s.replace(self.rs, "<root>").replace(str(self.root), "<root>").replace(os.path.expanduser("~"), "~")
        return re.sub(r"file://<root>", "file://<root>", s)

    def err(self, r):
        return "ERR:" + str(r["__error__"])[:60] if isinstance(r, dict) and "__error__" in r else None

    def rng(self, r):
        if not r:
            return "?"
        s, e = r["start"], r["end"]
        return f"{s['line']}:{s['character']}-{e['line']}:{e['character']}"

    def loc(self, x):
        if not isinstance(x, dict):
            return None
        uri = x.get("uri") or x.get("targetUri")
        if not uri:
            return None
        o, p = self.dep(*self.norm.path(uri))
        return f"{o}:{p}:{self.rng(x.get('range') or x.get('targetSelectionRange') or x.get('targetRange'))}"

    @staticmethod
    def dep(o, p):
        """Dependency sources: jar entry 'x.jar!/a/b.clj' and lsp-mode style copy 'a.b.clj' are the same file -> 'dep:a/b.clj'."""
        if o == "jar":
            return "dep", p.split("!/", 1)[-1]
        if o == "depcopy":
            stem, _, ext = p.rpartition(".")
            return "dep", stem.replace(".", "/") + "." + ext
        return o, p

    def locs(self, res):
        if res is None:
            return None
        if isinstance(res, dict) and "uri" in res or isinstance(res, dict) and "targetUri" in res:
            res = [res]
        return sorted({self.loc(x) for x in res if isinstance(x, dict)} - {None})

    def wedit(self, we):
        if not we:
            return None
        out = []
        for uri, es in (we.get("changes") or {}).items():
            o, p = self.dep(*self.norm.path(uri))
            out += [f"{o}:{p}:{self.rng(e['range'])}=>{e['newText']}" for e in es]
        for dc in we.get("documentChanges") or []:
            if "textDocument" in dc:
                o, p = self.dep(*self.norm.path(dc["textDocument"]["uri"]))
                out += [f"{o}:{p}:{self.rng(e['range'])}=>{e['newText']}" for e in dc.get("edits", [])]
            else:
                out.append("op:" + json.dumps({k: (self.norm.path(v) if k.endswith("ri") else v) for k, v in dc.items()}, sort_keys=True, default=str))
        return sorted(set(out))

    def dsym(self, syms, depth=0):
        out = []
        for s in syms or []:
            if "location" in s:  # SymbolInformation
                out.append(f"{s.get('name')}|{s.get('kind')}|{self.loc(s['location'])}|{s.get('containerName')}")
                continue
            out.append(f"{'.' * depth}{s.get('name')}|{s.get('kind')}|{self.rng(s.get('range'))}|{self.rng(s.get('selectionRange'))}|{self.san(s.get('detail'))}")
            out += self.dsym(s.get("children"), depth + 1)
        return sorted(out)

    def diag(self, d):
        return f"{self.rng(d['range'])}:{d.get('severity')}:{d.get('code')}:{d.get('source')}:{d.get('message')}"

    def semtok(self, data):
        leg = self.legend or {}
        ty, mo = leg.get("tokenTypes", []), leg.get("tokenModifiers", [])
        out, line, ch = [], 0, 0
        for i in range(0, len(data) - 4, 5):
            dl, dc, ln, t, m = data[i:i + 5]
            line, ch = line + dl, (ch + dc if dl == 0 else dc)
            mods = [mo[b] if b < len(mo) else str(b) for b in range(16) if m >> b & 1]
            out.append(f"{line}:{ch}:{ln}:{ty[t] if t < len(ty) else t}:{','.join(mods)}")
        return out

    # ---- probe building ----
    def tokens(self, rel):
        from run import tokenize
        text = self.text[rel]
        st = [0] + [m.end() for m in re.finditer("\n", text)]
        res = []
        for off, tok in tokenize(text):
            ln = max(k for k, s0 in enumerate(st) if s0 <= off)
            res.append((ln, off - st[ln], tok))
        return res

    def heads(self, rel):
        """Positions just inside a call, after the head symbol + one space: [(line, col)]."""
        text, out = self.text[rel], []
        st = [0] + [m.end() for m in re.finditer("\n", text)]
        for m in re.finditer(r"\(([^\s()\[\]{}\"';]+)[ \t]", text):
            off = m.end()
            ln = max(k for k, s0 in enumerate(st) if s0 <= off)
            out.append((ln, off - st[ln]))
        return out

    def batch1(self, rels):
        c, reqs, meta = self.c, [], []
        def add(key, method, params, norm):
            reqs.append((method, params))
            meta.append((key, method, norm))
        for rel in rels:
            uri = (self.root / rel).as_uri()
            td = {"textDocument": {"uri": uri}}
            toks = self.tokens(rel)
            n = len(self.text[rel].splitlines())
            uniq = list({t[2]: t for t in toks}.values())
            pos = lambda ln, ch: {**td, "position": {"line": ln, "character": ch}}
            add(f"{rel}:documentSymbol", "textDocument/documentSymbol", td, lambda r: self.dsym(r))
            add(f"{rel}:foldingRange", "textDocument/foldingRange", td,
                lambda r: sorted(f"{x['startLine']}:{x.get('startCharacter')}-{x['endLine']}:{x.get('endCharacter')}:{x.get('kind')}" for x in r or []))
            add(f"{rel}:codeLens", "textDocument/codeLens", td, None)  # raw, resolved in batch 2
            add(f"{rel}:semanticTokensFull", "textDocument/semanticTokens/full", td, lambda r: self.semtok((r or {}).get("data", [])))
            rg = {"start": {"line": 2, "character": 0}, "end": {"line": max(3, n - 3), "character": 0}}
            add(f"{rel}:semanticTokensRange", "textDocument/semanticTokens/range", {**td, "range": rg}, lambda r: self.semtok((r or {}).get("data", [])))
            opts = {"tabSize": 2, "insertSpaces": True}
            add(f"{rel}:formatting", "textDocument/formatting", {**td, "options": opts}, lambda r, rel=rel: self.fmt(rel, r))
            rg2 = {"start": {"line": 1, "character": 0}, "end": {"line": max(2, min(n - 1, 7)), "character": 0}}
            add(f"{rel}:rangeFormatting", "textDocument/rangeFormatting", {**td, "range": rg2, "options": opts}, lambda r, rel=rel: self.fmt(rel, r))
            for ln, ch, tok in sample(uniq, 12):
                add(f"{rel}:{ln}:{ch}:documentHighlight", "textDocument/documentHighlight", pos(ln, ch),
                    lambda r: None if r is None else sorted(f"{self.rng(x['range'])}:{x.get('kind')}" for x in r))
            for ln, ch in sample(self.heads(rel), 10):
                add(f"{rel}:{ln}:{ch}:signatureHelp", "textDocument/signatureHelp", pos(ln, ch), self.sig)
            for ln, ch, tok in sample(uniq, 8):
                add(f"{rel}:{ln}:{ch}:selectionRange", "textDocument/selectionRange", {**td, "positions": [{"line": ln, "character": ch}]}, self.selr)
                add(f"{rel}:{ln}:{ch}:linkedEditingRange", "textDocument/linkedEditingRange", pos(ln, ch),
                    lambda r: None if not r else {"ranges": sorted(self.rng(x) for x in r.get("ranges", [])), "wordPattern": r.get("wordPattern")})
            for ln, ch, tok in uniq:
                add(f"{rel}:{ln}:{ch}:prepareRename", "textDocument/prepareRename", pos(ln, ch), self.prep)
                add(f"{rel}:{ln}:{ch}:declaration", "textDocument/declaration", pos(ln, ch), self.locs)
                add(f"{rel}:{ln}:{ch}:implementation", "textDocument/implementation", pos(ln, ch), self.locs)
            for ln, ch, tok in self.rename_targets(uniq):
                add(f"{rel}:{ln}:{ch}:rename", "textDocument/rename", {**pos(ln, ch), "newName": RENAME_TO}, self.wedit)
            for ln, ch, tok in sample([t for t in uniq if re.match(r"^[a-zA-Z>*_-]", t[2])], 8):
                add(f"{rel}:{ln}:{ch}:callHierarchyPrepare", "textDocument/prepareCallHierarchy", pos(ln, ch), self.chitems)
            # codeAction: each diagnostic + a few positions
            for d in self.c.diags.get((self.root / rel).as_uri(), []):
                add(f"{rel}:{d['range']['start']['line']}:{d['range']['start']['character']}:codeAction", "textDocument/codeAction",
                    {**td, "range": d["range"], "context": {"diagnostics": [d]}}, self.actions)
            for ln, ch, tok in sample(uniq, 6):
                add(f"{rel}:{ln}:{ch}:codeActionPos", "textDocument/codeAction",
                    {**td, "range": {"start": {"line": ln, "character": ch}, "end": {"line": ln, "character": ch}}, "context": {"diagnostics": []}}, self.actions)
            # completion for batch 2 resolve
            for ln, ch, tok in [t for t in uniq if len(t[2]) >= 2 and not t[2].startswith(":")][:3]:
                add(f"{rel}:{ln}:{ch + 2}:completionRaw", "textDocument/completion", pos(ln, ch + 2), None)
        for q in WS_QUERIES:
            add(f"ws:{q}:workspaceSymbol", "workspace/symbol", {"query": q},
                lambda r: None if r is None else sorted(self.dsym(r))[:60])
        return reqs, meta

    def rename_targets(self, uniq):
        picks = sample(uniq, 6) + [t for t in uniq if t[2].startswith(":")][:2] + [t for t in uniq if "/" in t[2] and not t[2].startswith(":")][:3]
        defs = [t for i, t in enumerate(uniq) if i and uniq[i - 1][2] in ("defn", "defn-", "defrecord", "defprotocol", "defmacro", "defmulti")][:3]
        seen, out = set(), []
        for t in picks + defs:
            if (t[0], t[1]) not in seen:
                seen.add((t[0], t[1]))
                out.append(t)
        return out

    def fmt(self, rel, r):
        e = self.err(r)
        if e or r is None:
            return e
        return apply_edits(self.text[rel], r)

    def prep(self, r):
        if r is None:
            return None
        if "range" in r:
            return {"range": self.rng(r["range"]), "placeholder": r.get("placeholder")}
        return self.rng(r) if "start" in r else r

    def sig(self, r):
        if not r:
            return None
        return {"active": r.get("activeSignature"), "param": r.get("activeParameter"),
                "sigs": [{"label": s.get("label"), "param": s.get("activeParameter"),
                          "params": [p.get("label") for p in s.get("parameters", [])]} for s in r.get("signatures", [])]}

    def selr(self, r):
        out = []
        for x in r or []:
            chain = []
            while x:
                chain.append(self.rng(x["range"]))
                x = x.get("parent")
            out.append(">".join(chain))
        return out

    def chitems(self, r):
        if r is None:
            return None
        return sorted({f"{i.get('name')}|{i.get('kind')}|{self.loc(i)}|{self.san(i.get('detail'))}" for i in r})

    def actions(self, r):
        if r is None:
            return None
        out = []
        for a in r:
            if "title" not in a or "kind" not in a and "edit" not in a and "command" not in a:
                continue
            cmd = a.get("command")
            cmd = cmd.get("command") if isinstance(cmd, dict) else cmd
            out.append({"title": a.get("title"), "kind": a.get("kind"), "edit": self.wedit(a.get("edit")), "command": cmd})
        return sorted(out, key=lambda x: json.dumps(x, sort_keys=True))

    def item(self, it):
        doc = it.get("documentation")
        doc = doc.get("value") if isinstance(doc, dict) else doc
        return {"label": it.get("label"), "kind": it.get("kind"), "detail": self.san(it.get("detail")), "doc": self.san((doc or "")[:300]),
                "insert": it.get("insertText"), "extra": len(it.get("additionalTextEdits") or [])}

    # ---- run ----
    def run(self, rels):
        c, out = self.c, {}
        reqs, meta = self.batch1(rels)
        futs = c.send_batch(reqs)
        dl = time.monotonic() + DEADLINE_S
        res = [self.wait_all([f], max(0.01, dl - time.monotonic()))[0] for f in futs]
        raws = {k: r for (k, _, _), r in zip(meta, res)}
        for (k, m, norm), r in zip(meta, res):
            if norm is None:
                continue
            out[k] = self.err(r) or norm(r)
        # batch 2: codeLens resolve, completionItem resolve, call hierarchy in/out
        reqs2, meta2 = [], []
        for (k, m, norm), r in zip(meta, res):
            rel = k.rsplit(":", 1)[0]
            if k.endswith(":codeLens"):
                lenses = r if isinstance(r, list) else []
                out[k] = self.err(r) or sorted(self.rng(x["range"]) for x in lenses)
                for i, l in enumerate(sorted(lenses, key=lambda x: self.rng(x["range"]))):
                    reqs2.append(("codeLens/resolve", l))
                    meta2.append((f"{rel}:{l['range']['start']['line']}:{l['range']['start']['character']}:codeLensResolve", self.lens))
            elif k.endswith(":completionRaw"):
                items = (r.get("items", []) if isinstance(r, dict) else r) if not self.err(r) else []
                out[k] = self.err(r) or sorted(i.get("label", "") for i in items)[:30]
                for i in sorted(items, key=lambda x: x.get("label", ""))[:3]:
                    reqs2.append(("completionItem/resolve", i))
                    meta2.append((f"{k.rsplit(':', 1)[0]}:{i.get('label')}:completionItemResolve", self.resolved))
            elif k.endswith(":callHierarchyPrepare") and isinstance(r, list) and r:
                it = sorted(r, key=lambda x: self.loc(x) or "")[0]
                base = k.rsplit(":", 1)[0]
                reqs2.append(("callHierarchy/incomingCalls", {"item": it}))
                meta2.append((f"{base}:callHierarchyIncoming", self.inc))
                reqs2.append(("callHierarchy/outgoingCalls", {"item": it}))
                meta2.append((f"{base}:callHierarchyOutgoing", self.outg))
        futs = c.send_batch(reqs2)
        dl = time.monotonic() + DEADLINE_S
        for (k, norm), f in zip(meta2, futs):
            r = self.wait_all([f], max(0.01, dl - time.monotonic()))[0]
            out[k] = self.err(r) or norm(r)
        return out

    def lens(self, r):
        cmd = (r or {}).get("command") or {}
        return {"range": self.rng((r or {}).get("range")), "title": cmd.get("title"), "command": cmd.get("command")}

    def resolved(self, r):
        return self.item(r) if isinstance(r, dict) else r

    def inc(self, r):
        if r is None:
            return None
        return sorted({f"{x['from'].get('name')}|{self.loc(x['from'])}|{sorted(self.rng(y) for y in x.get('fromRanges', []))}" for x in r})

    def outg(self, r):
        if r is None:
            return None
        return sorted({f"{x['to'].get('name')}|{self.loc(x['to'])}|{sorted(self.rng(y) for y in x.get('fromRanges', []))}" for x in r})


def project_diagnostics(c, root, norm):
    """After a no-open startup: every uri the server published for, project files only (normalized, full diagnostics)."""
    e = Ext(c, root, norm, [], None, None)
    out = {}
    for uri, ds in c.diags.items():
        o, p = norm.path(uri)
        if o == "project":
            out[f"{p}:projectDiagnostics"] = sorted(e.diag(d) for d in ds)
    return out
