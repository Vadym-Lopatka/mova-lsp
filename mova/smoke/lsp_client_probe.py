#!/usr/bin/env python3
"""lsp/host stage 3 (mova/PLAN.md): drives the REAL clojure-lsp server (JVM
or Mova) over stdio exactly like a language client -- initialize/initialized,
didOpen + wait for diagnostics, hover/completion/definition/references/
documentSymbol/formatting/rename/codeAction, shutdown/exit. Runs on a FRESH
copy of cli/integration-test/sample-test (never the shared fixture) so the
session can freely edit .cpcache/.lsp caches without polluting other smoke
runs or git status.

Handles the three server->client requests real editors answer:
  - "window/workDoneProgress/create" -> {} (result: null)
  - "client/registerCapability"      -> {} (result: null)
  - "workspace/configuration"        -> one `null` per requested item

Prints a NORMALIZED transcript to stdout (one JSON value per line, sorted
keys -- same "values, not bytes" oracle rule as drive_jsonrpc.py): the fresh
tempdir root is rewritten to the literal "<root>" (in both file:// URIs and
plain paths) so a new tempdir path each run never shows as a diff, and
$/progress / window/logMessage notifications are dropped (pure timing/log
noise, not protocol behavior worth oracle-diffing). Diagnostics arrays and
completion-item lists are sorted (element order is non-deterministic --
depends on internal map/thread-scheduling order on both JVM and Mova --
while every editor treats them as sets/renders them sorted anyway).

Timings (spawn->initialize reply, spawn->first diagnostics, total, peak
child RSS via `resource.getrusage(RUSAGE_CHILDREN)`) go to STDERR only, so
they never pollute the oracle-diffed transcript.

Usage:
    python3 lsp_client.py --server-cmd "mova/bin/clojure-lsp" [--cwd DIR] [--timeout SECS]
    MOVA_BIN=<bin> python3 mova/smoke/lsp_client.py --server-cmd "mova/bin/clojure-lsp"
    python3 mova/smoke/lsp_client.py --server-cmd "clojure -M -m clojure-lsp.main" --cwd cli
"""
import argparse
import json
import os
import queue
import resource
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

DEFAULT_TIMEOUT = 60.0
# Anchored to this script's own location (mova/smoke/lsp_client.py), not to
# `--cwd` (the JVM server needs `--cwd cli`; Mova's launcher needs the repo
# root) -- so the fixture resolves the same regardless of where the server
# process itself is spawned from.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO_ROOT, "cli", "integration-test", "sample-test")


def frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class Reader(threading.Thread):
    """Background thread: frames off `stream` land on `out` as parsed dicts,
    keeping the main thread free to send requests and answer server-
    initiated ones without a deadlock (a real client is event-driven, not
    request/reply-only -- `client/registerCapability` etc. can arrive
    between our own requests)."""

    def __init__(self, stream, out: "queue.Queue"):
        super().__init__(daemon=True)
        self.stream = stream
        self.out = out

    def run(self):
        buf = bytearray()
        while True:
            headers = {}
            line = bytearray()
            while True:
                b = self.stream.read(1)
                if not b:
                    self.out.put(None)  # EOF sentinel
                    return
                if b == b"\n":
                    text = bytes(line).decode("ascii", "replace")
                    if text == "":
                        break
                    if ":" in text:
                        k, v = text.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                    line = bytearray()
                elif b != b"\r":
                    line += b
            n = int(headers["content-length"])
            body = bytearray()
            while len(body) < n:
                chunk = self.stream.read(n - len(body))
                if not chunk:
                    self.out.put(None)
                    return
                body += chunk
            try:
                self.out.put(json.loads(bytes(body).decode("utf-8")))
            except json.JSONDecodeError:
                pass


def sum_rss_tree(root_pid: int) -> int:
    """Sums RSS (KB) over `root_pid` and its descendants via `ps` -- the
    server may itself be a shell wrapper around a real JVM/binary child
    (e.g. `clojure -M ...` -> `java`), so the launcher's own PID alone
    would undercount."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return 0
    children = {}
    rss = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        pid, ppid, r = (int(x) for x in parts)
        children.setdefault(ppid, []).append(pid)
        rss[pid] = r
    total = 0
    stack = [root_pid]
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += rss.get(pid, 0)
        stack.extend(children.get(pid, []))
    return total


def uri(path: str) -> str:
    return "file://" + path


def calva_capabilities() -> dict:
    """A realistic subset of what VS Code + Calva actually announce (trimmed
    to the fields clojure-lsp's `capabilities` fn / handlers branch on)."""
    return {
        "workspace": {
            "applyEdit": True,
            "workspaceEdit": {"documentChanges": True, "resourceOperations": ["create", "rename", "delete"]},
            "didChangeConfiguration": {"dynamicRegistration": True},
            "didChangeWatchedFiles": {"dynamicRegistration": True},
            "symbol": {"dynamicRegistration": True},
            "executeCommand": {"dynamicRegistration": True},
            "configuration": True,
            "workspaceFolders": True,
        },
        "textDocument": {
            "synchronization": {"dynamicRegistration": True, "willSave": True, "didSave": True},
            "completion": {
                "dynamicRegistration": True,
                "completionItem": {"snippetSupport": True, "documentationFormat": ["markdown", "plaintext"]},
                "contextSupport": True,
            },
            "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
            "signatureHelp": {"dynamicRegistration": True},
            "definition": {"dynamicRegistration": True},
            "references": {"dynamicRegistration": True},
            "documentSymbol": {"dynamicRegistration": True, "hierarchicalDocumentSymbolSupport": True},
            "formatting": {"dynamicRegistration": True},
            "rangeFormatting": {"dynamicRegistration": True},
            "rename": {"dynamicRegistration": True, "prepareSupport": True},
            "publishDiagnostics": {"relatedInformation": True, "versionSupport": True},
            "codeAction": {
                "dynamicRegistration": True,
                "codeActionLiteralSupport": {
                    "codeActionKind": {
                        "valueSet": ["quickfix", "refactor", "refactor.extract", "refactor.inline", "refactor.rewrite", "source"]
                    }
                },
            },
        },
        "window": {"workDoneProgress": True},
    }


class Session:
    def __init__(self, server_cmd, cwd, timeout, project=None):
        self.timeout = timeout
        # realpath: clojure-lsp canonicalizes `rootUri`/file URIs (fs/canonicalize),
        # and on macOS `tempfile.mkdtemp()` returns a path under the `/tmp` ->
        # `/private/tmp` (or `/var` -> `/private/var`) symlink, which would
        # otherwise never match the server's published-diagnostics URIs.
        if project:
            # Reuse a given project dir as-is (warm-start runs) instead of a
            # fresh temp copy -- lets the server's on-disk cache persist
            # between successive sessions on the same directory.
            self.root = os.path.realpath(project)
        else:
            self.root = os.path.realpath(tempfile.mkdtemp(prefix="lsp_client_sample_test_"))
            shutil.copytree(FIXTURE, self.root, dirs_exist_ok=True)
        env = dict(os.environ)
        self.t_spawn = time.time()
        self.proc = subprocess.Popen(
            shlex.split(server_cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd
        )
        self.q: "queue.Queue" = queue.Queue()
        self.reader = Reader(self.proc.stdout, self.q)
        self.reader.start()
        # Drain stderr continuously -- the server logs verbosely (kondo,
        # java-interop, clojuredocs...) and an undrained pipe fills its OS
        # buffer (~64KB), which then blocks the server's own log write(2)
        # and deadlocks the whole session (looks identical to a server
        # hang from the client side: no more stdout ever arrives).
        self.stderr_buf = bytearray()
        self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()
        self.next_id = 1
        self.transcript = []
        self.diagnostics = {}  # uri -> latest publishDiagnostics params
        self.diag_events = {}  # uri -> threading.Event, set on first diagnostics
        self.t_init_reply = None
        self.t_first_diag = None
        self.rss_at_init_kb = None

    def _drain_stderr(self):
        while True:
            chunk = self.proc.stderr.read(65536)
            if not chunk:
                return
            self.stderr_buf += chunk

    # -- low-level send/recv --------------------------------------------
    def _send(self, obj):
        self.proc.stdin.write(frame(obj))
        self.proc.stdin.flush()

    def request(self, method, params, record=True):
        mid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        # Recorded at SEND time (not after the reply) so the transcript's
        # own order reflects wall-clock order -- any server notification
        # that happens to arrive while we're still waiting for this
        # request's response (e.g. JVM's project-wide startup diagnostics
        # racing the first `hover` call) must not appear to precede it.
        if record:
            self.transcript.append({"dir": "client->server-request", "method": method, "params": params})
        r = self._await_response(mid, method)
        rss_mb = sum_rss_tree(self.proc.pid) / 1024.0
        print(f"RSSPROBE {method} {rss_mb:.1f}MB", file=sys.stderr)
        return r

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})
        self.transcript.append({"dir": "client->server", "method": method, "params": params})

    def _handle_server_request(self, msg):
        method = msg["method"]
        self.transcript.append({"dir": "server->client-request", "method": method, "params": msg.get("params")})
        if method == "workspace/configuration":
            n = len(msg.get("params", {}).get("items", []))
            result = [None] * n
        elif method in ("window/workDoneProgress/create", "client/registerCapability"):
            result = None
        else:
            result = None
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _handle_notification(self, msg):
        method = msg["method"]
        if method in ("$/progress", "window/logMessage"):
            return  # pure timing/log noise -- dropped from the transcript
        params = msg.get("params", {})
        if method == "textDocument/publishDiagnostics":
            u = params.get("uri")
            # completion/b.clj and declaration/b.clj declare the SAME ns; under
            # clj-kondo :parallel which file gets refer-all is timing-dependent
            # (on the JVM too), so drop that one finding for those two files.
            if u and u.endswith(("/completion/b.clj", "/declaration/b.clj")):
                params = dict(params, diagnostics=[d for d in params.get("diagnostics", []) if d.get("code") != "refer-all"])
            self.diagnostics[u] = params
            if self.t_first_diag is None:
                self.t_first_diag = time.time()
            ev = self.diag_events.get(u)
            if ev is not None:
                ev.set()
        self.transcript.append({"dir": "server->client-notification", "method": method, "params": params})

    def _pump_until(self, pred, deadline):
        """Drains the reader queue, dispatching requests/notifications,
        until `pred()` is true or `deadline` passes. Returns the matching
        response dict, or None on timeout."""
        while time.time() < deadline:
            try:
                msg = self.q.get(timeout=max(0.0, deadline - time.time()))
            except queue.Empty:
                break
            if msg is None:
                raise RuntimeError("server closed stdout (EOF) while waiting")
            if "method" in msg and "id" in msg:
                self._handle_server_request(msg)
            elif "method" in msg:
                self._handle_notification(msg)
                if pred(msg):
                    return msg
            else:
                hit = pred(msg)
                if hit:
                    return msg
        return None

    def _await_response(self, mid, method):
        t0 = time.time()
        deadline = t0 + self.timeout
        resp = self._pump_until(lambda m: m.get("id") == mid, deadline)
        if resp is None:
            raise TimeoutError(f"timed out waiting for response to {method} (id={mid})")
        print(f"[req] {method} {(time.time() - t0) * 1000:.0f} ms", file=sys.stderr)
        self.transcript.append({"dir": "server->client-response", "method": method, "result": resp.get("result"), "error": resp.get("error")})
        return resp

    def wait_for_diagnostics(self, file_uri, timeout=None):
        deadline = time.time() + (timeout or self.timeout)
        if file_uri in self.diagnostics:
            return self.diagnostics[file_uri]
        ev = self.diag_events.setdefault(file_uri, threading.Event())
        t0 = time.time()
        self._pump_until(lambda m: file_uri in self.diagnostics, deadline)
        got = file_uri in self.diagnostics
        print(f"[diag] {file_uri.rsplit('/', 1)[-1]} {'ok' if got else 'TIMEOUT'} {(time.time() - t0) * 1000:.0f} ms; have {len(self.diagnostics)} uris", file=sys.stderr)
        return self.diagnostics.get(file_uri)

    def path(self, *parts):
        return os.path.join(self.root, *parts)


def run_session(server_cmd, cwd, timeout, project=None, full=False):
    s = Session(server_cmd, cwd, timeout, project=project)

    # Generic mode: `--project` points at a real deps.edn project (e.g.
    # `lib/`), not a copy of the sample-test fixture, so the fixture's
    # hardcoded relative paths don't exist. Probe one real file instead,
    # at a known external-var call site (`clojure.string/replace-first`,
    # `lib/src/clojure_lsp/parser.clj:42`) to check external-jar analysis
    # (org.clojure/clojure itself is a jar on the classpath).
    generic = not os.path.isfile(s.path("src", "sample_test", "definition", "a.clj"))
    if generic:
        target = s.path("src", "clojure_lsp", "parser.clj")
        files = {"a": target, "b": target, "fmt": target}
        probe_pos = {"line": 41, "character": 26}  # mid `replace-first` token
        completion_pos = {"line": 41, "character": 23}  # just past `string/`
    else:
        files = {
            "a": s.path("src", "sample_test", "definition", "a.clj"),
            "b": s.path("src", "sample_test", "definition", "b.clj"),
            "fmt": s.path("src", "sample_test", "formatting.clj"),
        }
        probe_pos = {"line": 3, "character": 6}
        completion_pos = {"line": 3, "character": 9}

    # 1. initialize -- realistic VS Code/Calva capabilities.
    init_resp = s.request(
        "initialize",
        {
            "processId": os.getpid(),
            "clientInfo": {"name": "Visual Studio Code", "version": "1.90.0"},
            "locale": "en",
            "rootUri": uri(s.root),
            "rootPath": s.root,
            "workspaceFolders": [{"uri": uri(s.root), "name": os.path.basename(s.root)}],
            "capabilities": calva_capabilities(),
            # `full=True` (the `--full` flag) omits `projectSpecs` entirely so
            # the server runs its DEFAULT startup path: `clojure -Spath`
            # classpath discovery + external jar analysis, same as a real
            # editor. Smoke runs pass `projectSpecs: []` to skip that (fast,
            # project-only analysis).
            "initializationOptions": ({"log-path": os.environ["PROBE_LOG"]} if full and os.environ.get("PROBE_LOG") else {} if full else {"projectSpecs": []}),
        },
    )
    s.t_init_reply = time.time()
    # Perf target's own measurement point ("RSS <= 80MB at initialize
    # reply") -- a live `ps` poll of the (possibly multi-process, e.g. a
    # JVM launcher shelling to `java`) process tree, taken immediately,
    # not the whole-session peak `getrusage` reports at the very end.
    s.rss_at_init_kb = sum_rss_tree(s.proc.pid)
    if init_resp.get("error"):
        s.notify("exit", {})
        return s, init_resp

    # 2. initialized
    s.notify("initialized", {})

    # 3. didOpen sample/probe file(s) + wait for diagnostics on each
    # (generic mode: "a"/"b"/"fmt" all alias the same real file -- open once).
    open_paths = sorted(set(files.values()))
    for path in open_paths:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        s.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri(path), "languageId": "clojure", "version": 1, "text": text}},
        )
    for path in open_paths:
        s.wait_for_diagnostics(uri(path))

    # 4. hover -- generic mode: `clojure.string/replace-first` call site
    # (external jar var); fixture mode: definition/b.clj `some-public-func`.
    s.request("textDocument/hover", {"textDocument": {"uri": uri(files["b"])}, "position": probe_pos})

    # 5. completion -- just past `string/` (generic) / `some-var` (fixture).
    s.request(
        "textDocument/completion",
        {"textDocument": {"uri": uri(files["a"])}, "position": completion_pos, "context": {"triggerKind": 1}},
    )

    # 6. definition -- same position as hover; generic mode checks external-
    # jar analysis (should resolve into the vendored/jar clojure.string source).
    s.request("textDocument/definition", {"textDocument": {"uri": uri(files["b"])}, "position": probe_pos})

    # 7. references -- on the probed symbol.
    s.request("textDocument/references", {"textDocument": {"uri": uri(files["a"])}, "position": probe_pos, "context": {"includeDeclaration": True}})

    # 8. documentSymbol.
    s.request("textDocument/documentSymbol", {"textDocument": {"uri": uri(files["a"])}})

    # 9. formatting.
    s.request(
        "textDocument/formatting",
        {"textDocument": {"uri": uri(files["fmt"])}, "options": {"tabSize": 2, "insertSpaces": True}},
    )

    if not generic:
        # 10. rename -- prepare then rename `some-var` (def + one use, same file).
        # Skipped in generic mode: the probe position is an external jar var,
        # not a project-defined one (rename there should just no-op/error).
        s.request("textDocument/prepareRename", {"textDocument": {"uri": uri(files["a"])}, "position": probe_pos})
        s.request(
            "textDocument/rename",
            {"textDocument": {"uri": uri(files["a"])}, "position": probe_pos, "newName": "renamed-var"},
        )

        # 11. codeAction -- inside `foo` in formatting.clj.
        s.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri(files["fmt"])},
                "range": {"start": {"line": 2, "character": 5}, "end": {"line": 2, "character": 8}},
                "context": {"diagnostics": []},
            },
        )

    # 12. shutdown + exit.
    s.request("shutdown", {})
    s.notify("exit", {})
    return s, init_resp


def normalize_value(v, root):
    """Recursively: rewrites the fresh tempdir root to "<root>" (both plain
    paths and file:// URIs), and sorts arrays whose element order isn't
    protocol-meaningful (diagnostics, completion items, references,
    documentSymbol children, workspaceEdit changes) -- rendered as SETS by
    every real editor, so sort order is the only order that oracle-diffs
    cleanly across JVM/Mova's different internal map/thread scheduling."""
    if isinstance(v, str):
        return v.replace(uri(root), "<root>").replace(root, "<root>")
    if isinstance(v, dict):
        return {k: normalize_value(val, root) for k, val in sorted(v.items())}
    if isinstance(v, list):
        items = [normalize_value(x, root) for x in v]
        try:
            items.sort(key=lambda x: json.dumps(x, sort_keys=True))
        except TypeError:
            pass
        return items
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-cmd", required=True)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--project", default=None, help="reuse this dir instead of a fresh temp copy (warm-start runs)")
    ap.add_argument("--full", action="store_true", help="omit projectSpecs -- default startup, full classpath discovery + external jar analysis")
    args = ap.parse_args()

    cwd = os.path.abspath(args.cwd)
    s, init_resp = run_session(args.server_cmd, cwd, args.timeout, project=args.project, full=args.full)

    try:
        s.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        s.proc.kill()
        s.proc.wait(timeout=5)
    t_end = time.time()

    for entry in s.transcript:
        print(json.dumps(normalize_value(entry, s.root), sort_keys=True, ensure_ascii=False))

    sys.stderr.write(s.stderr_buf.decode("utf-8", "replace"))

    rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_rss_mb = rss_kb / 1024 if sys.platform == "darwin" else rss_kb / 1024  # ru_maxrss: bytes on darwin, KB on linux
    if sys.platform != "darwin":
        peak_rss_mb = rss_kb / 1024
    else:
        peak_rss_mb = rss_kb / (1024 * 1024)

    def ms(t):
        return None if t is None else round((t - s.t_spawn) * 1000, 1)

    print(
        json.dumps(
            {
                "spawn_to_initialize_reply_ms": ms(s.t_init_reply),
                "spawn_to_first_diagnostics_ms": ms(s.t_first_diag),
                "total_ms": ms(t_end),
                "rss_at_initialize_reply_mb": round((s.rss_at_init_kb or 0) / 1024, 1),
                "peak_rss_whole_session_mb": round(peak_rss_mb, 1),
            }
        ),
        file=sys.stderr,
    )
    if not args.project:
        shutil.rmtree(s.root, ignore_errors=True)


if __name__ == "__main__":
    main()
