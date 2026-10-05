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
    # one project, one file opened right after `initialized` (no settle); DIR is used
    # in place so a 2nd run is warm; stdout = final diagnostics per uri:
    python3 mova/smoke/lsp_client.py --server-cmd mova/bin/clojure-lsp --root DIR \
        --open src/clojure_lsp/handlers.clj [--wait-background] [--quiet 3] [--stderr-out FILE]
"""
import argparse
import json
import os
import queue
import re
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


def tree_pids(root_pid: int) -> list:
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return [root_pid]
    children = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    pids, stack = [], [root_pid]
    while stack:
        pid = stack.pop()
        if pid not in pids:
            pids.append(pid)
            stack.extend(children.get(pid, []))
    return pids


def snap_mem(pid, tag):
    """LSP_SNAP_DIR=<dir>: save `footprint` (categories) and `vmmap -summary` of the server."""
    d = os.environ.get("LSP_SNAP_DIR")
    if not d:
        return
    os.makedirs(d, exist_ok=True)
    for p in tree_pids(pid):
        for name, cmd in (("footprint", ["footprint", str(p)]), ("vmmap", ["vmmap", "-summary", str(p)])):
            out = subprocess.run(cmd, capture_output=True, text=True).stdout
            open(os.path.join(d, f"{tag}-{name}-{p}.txt"), "w").write(out)


def footprint_tree(root_pid: int, tree=True):
    """(phys_footprint, phys_footprint_peak) in bytes summed over the process
    tree, via macOS `footprint` (what Activity Monitor shows; unlike ps RSS it
    drops MADV_FREE'd pages). The peak is the kernel's lifetime high-water
    mark, so no polling is needed. (None, None) where unavailable."""
    if sys.platform != "darwin":
        return None, None
    try:
        out = subprocess.run(["footprint", "--noCategories", "-f", "bytes"] + [str(p) for p in (tree_pids(root_pid) if tree else [root_pid])],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None, None
    cur = sum(int(m) for m in re.findall(r"phys_footprint: (\d+) B", out))
    peak = sum(int(m) for m in re.findall(r"phys_footprint_peak: (\d+) B", out))
    return (cur or None), (peak or None)


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
    def __init__(self, server_cmd, cwd, timeout, root=None):
        self.timeout = timeout
        # realpath: clojure-lsp canonicalizes `rootUri`/file URIs (fs/canonicalize),
        # and on macOS `tempfile.mkdtemp()` returns a path under the `/tmp` ->
        # `/private/tmp` (or `/var` -> `/private/var`) symlink, which would
        # otherwise never match the server's published-diagnostics URIs.
        if root:
            # --root: use DIR in place (no copy) so a 2nd run sees its caches (warm).
            self.root = os.path.realpath(root)
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
        self.log_lines = []
        self.wait_log_re = None
        self.wait_log_hit = None
        self.diagnostics = {}  # uri -> latest publishDiagnostics params
        self.diag_events = {}  # uri -> threading.Event, set on first diagnostics
        self.t_init_reply = None
        self.t_first_diag = None
        self.rss_at_init_kb = None
        self.t_last_diag = None

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
        return self._await_response(mid, method)

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

    def settle(self, quiet):
        """Pump messages until none arrives for `quiet` seconds (max self.timeout)."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                msg = self.q.get(timeout=quiet)
            except queue.Empty:
                return
            if msg is None:
                raise RuntimeError("server closed stdout (EOF) while settling")
            if "method" in msg and "id" in msg:
                self._handle_server_request(msg)
            elif "method" in msg:
                self._handle_notification(msg)

    def _handle_notification(self, msg):
        method = msg["method"]
        if method in ("$/progress", "window/logMessage"):
            if method == "window/logMessage":
                m = msg.get("params", {}).get("message", "")
                self.log_lines.append((time.time(), m))
                if self.wait_log_re is not None and self.wait_log_re.search(m):
                    self.wait_log_hit = time.time()
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
            self.t_last_diag = time.time()
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
        deadline = time.time() + self.timeout
        resp = self._pump_until(lambda m: m.get("id") == mid, deadline)
        if resp is None:
            raise TimeoutError(f"timed out waiting for response to {method} (id={mid})")
        self.transcript.append({"dir": "server->client-response", "method": method, "result": resp.get("result"), "error": resp.get("error")})
        return resp

    def wait_for_diagnostics(self, file_uri, timeout=None):
        deadline = time.time() + (timeout or self.timeout)
        if file_uri in self.diagnostics:
            return self.diagnostics[file_uri]
        ev = self.diag_events.setdefault(file_uri, threading.Event())
        self._pump_until(lambda m: file_uri in self.diagnostics, deadline)
        return self.diagnostics.get(file_uri)

    def path(self, *parts):
        return os.path.join(self.root, *parts)


def start_footprint_trace(s, path):
    def loop():
        with open(path, "w") as f:
            while s.proc.poll() is None:
                cur, _ = footprint_tree(s.proc.pid)
                if cur:
                    f.write("%9.1f %.1f\n" % ((time.time() - s.t_spawn) * 1000, cur / 1048576))
                    f.flush()
                time.sleep(0.5)
    threading.Thread(target=loop, daemon=True).start()


def run_open_session(server_cmd, cwd, timeout, root, open_file, quiet, wait_background=False, full=False, wait_log=None, footprint_trace=None):
    """--open mode: initialize, initialized, didOpen ONE file at once (no settle),
    wait for its diagnostics, then settle `quiet` s so background publishes land,
    shutdown. Transcript = the final published diagnostics per uri."""
    s = Session(server_cmd, cwd, timeout, root=root)
    if footprint_trace:
        start_footprint_trace(s, footprint_trace)
    s.request(
        "initialize",
        {
            "processId": os.getpid(),
            "clientInfo": {"name": "Visual Studio Code", "version": "1.90.0"},
            "locale": "en",
            "rootUri": uri(s.root),
            "rootPath": s.root,
            "workspaceFolders": [{"uri": uri(s.root), "name": os.path.basename(s.root)}],
            "capabilities": calva_capabilities(),
            "initializationOptions": {} if full else {"projectSpecs": []},
        },
        record=False,
    )
    s.t_init_reply = time.time()
    s.rss_at_init_kb = sum_rss_tree(s.proc.pid)
    s.notify("initialized", {})
    path = os.path.realpath(os.path.join(s.root, open_file))
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    s.notify("textDocument/didOpen", {"textDocument": {"uri": uri(path), "languageId": "clojure", "version": 1, "text": text}})
    s.wait_for_diagnostics(uri(path))
    s.t_open_diag = time.time()
    # footprint first: the background pass allocates fast right after this point
    t0 = time.time()
    s.fp_open_diag, _ = footprint_tree(s.proc.pid, tree=False)
    s.fp_open_diag_lag_ms = (time.time() - s.t_open_diag) * 1000
    s.rss_at_open_diag_kb = sum_rss_tree(s.proc.pid)
    snap_mem(s.proc.pid, "open")
    if wait_background:
        # background analysis ends with publish-all: wait for diagnostics of any OTHER uri
        # (needs another file with findings; else runs to --timeout)
        s._pump_until(lambda m: len(s.diagnostics) > 1, time.time() + timeout)
    if wait_log:
        # regex is matched against the server's own log file (--log-path, given via
        # server-cmd) named by wait_log = "FILE::REGEX"; polled every 0.2 s
        lf, rx = wait_log.split("::", 1)
        rx = re.compile(rx)
        end = time.time() + timeout
        while time.time() < end:
            s._pump_until(lambda m: False, time.time() + 0.2)
            try:
                if rx.search(open(lf, errors="replace").read()):
                    s.wait_log_hit = time.time()
                    break
            except OSError:
                pass
    s.settle(quiet)
    s.transcript = [{"uri": u, "diagnostics": p.get("diagnostics", [])} for u, p in s.diagnostics.items()]
    s.rss_at_end_kb = sum_rss_tree(s.proc.pid)  # after settle, before shutdown
    s.fp_end, s.fp_peak = footprint_tree(s.proc.pid)
    snap_mem(s.proc.pid, "end")
    s.request("shutdown", {}, record=False)
    s.notify("exit", {})
    return s, None


def run_session(server_cmd, cwd, timeout):
    s = Session(server_cmd, cwd, timeout)

    files = {
        "a": s.path("src", "sample_test", "definition", "a.clj"),
        "b": s.path("src", "sample_test", "definition", "b.clj"),
        "fmt": s.path("src", "sample_test", "formatting.clj"),
    }

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
            "initializationOptions": {"projectSpecs": []},
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
    # Mova replies to initialize before project analysis ends (the JVM blocks);
    # settle until the server is quiet so the background publish-all lands here.
    s.settle(0.5)

    # 3. didOpen 3 sample files + wait for diagnostics on each.
    for key in ("a", "b", "fmt"):
        path = files[key]
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        s.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri(path), "languageId": "clojure", "version": 1, "text": text}},
        )
    for key in ("a", "b", "fmt"):
        s.wait_for_diagnostics(uri(files[key]))

    # 4. hover -- definition/b.clj, inside the `some-public-func` call token.
    s.request("textDocument/hover", {"textDocument": {"uri": uri(files["b"])}, "position": {"line": 3, "character": 6}})

    # 5. completion -- inside the `some-var` token in definition/a.clj.
    s.request(
        "textDocument/completion",
        {"textDocument": {"uri": uri(files["a"])}, "position": {"line": 3, "character": 9}, "context": {"triggerKind": 1}},
    )

    # 6. definition -- same position as hover, should land in definition/a.clj.
    s.request("textDocument/definition", {"textDocument": {"uri": uri(files["b"])}, "position": {"line": 3, "character": 6}})

    # 7. references -- on `some-public-func`'s definition itself.
    s.request("textDocument/references", {"textDocument": {"uri": uri(files["a"])}, "position": {"line": 5, "character": 8}, "context": {"includeDeclaration": True}})

    # 8. documentSymbol -- definition/a.clj (several top-level forms).
    s.request("textDocument/documentSymbol", {"textDocument": {"uri": uri(files["a"])}})

    # 9. formatting -- formatting.clj (deliberately misindented fixture).
    s.request(
        "textDocument/formatting",
        {"textDocument": {"uri": uri(files["fmt"])}, "options": {"tabSize": 2, "insertSpaces": True}},
    )

    # 10. rename -- prepare then rename `some-var` (def + one use, same file).
    s.request("textDocument/prepareRename", {"textDocument": {"uri": uri(files["a"])}, "position": {"line": 3, "character": 6}})
    s.request(
        "textDocument/rename",
        {"textDocument": {"uri": uri(files["a"])}, "position": {"line": 3, "character": 6}, "newName": "renamed-var"},
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


_SET_LITERAL_RE = re.compile(r"#\{([^{}]*)\}")


def _norm_set_literals(s):
    """Diagnostic MESSAGE text embeds Clojure hash-set literals (e.g.
    "Different aliases #{s string cstring str}"); hash-set iteration order
    is unspecified (and differs between JVM and Mova), so sort each set's
    elements to make the text comparable."""
    return _SET_LITERAL_RE.sub(lambda m: "#{" + " ".join(sorted(m.group(1).split())) + "}", s)


def normalize_value(v, root):
    """Recursively: rewrites the fresh tempdir root to "<root>" (both plain
    paths and file:// URIs), the client's own pid and the root's random
    tempdir basename (both server-independent, differ every run/process --
    only appear in the client->server `initialize` request) to fixed
    placeholders, sorts each set literal embedded in message text (see
    `_norm_set_literals`), and sorts arrays whose element order isn't
    protocol-meaningful (diagnostics, completion items, references,
    documentSymbol children, workspaceEdit changes) -- rendered as SETS by
    every real editor, so sort order is the only order that oracle-diffs
    cleanly across JVM/Mova's different internal map/thread scheduling."""
    root_name = os.path.basename(root)
    if isinstance(v, str):
        v = v.replace(uri(root), "<root>").replace(root, "<root>").replace(root_name, "<root-name>")
        return _norm_set_literals(v)
    if isinstance(v, dict):
        return {
            k: ("<pid>" if k == "processId" else normalize_value(val, root))
            for k, val in sorted(v.items())
        }
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
    ap.add_argument("--root", help="project dir used in place (no copy); default: fresh copy of the sample fixture")
    ap.add_argument("--open", help="--open mode: open only this file (relative to root) right after initialized")
    ap.add_argument("--quiet", type=float, default=3.0, help="--open mode: settle seconds after its diagnostics")
    ap.add_argument("--stderr-out", help="write server stderr to this file")
    ap.add_argument("--wait-background", action="store_true", help="--open mode: before settling, wait for diagnostics of another uri (background publish-all)")
    ap.add_argument("--full", action="store_true", help="--open mode: omit projectSpecs [] (real classpath resolution + jar analysis)")
    ap.add_argument("--wait-log", help="--open mode: before settling, wait for a window/logMessage matching this regex")
    ap.add_argument("--footprint-trace", help="--open mode: every 0.5 s append 'ms_since_spawn footprint_mb' of the server to this file")
    ap.add_argument("--log-out", help="write window/logMessage lines (ms since spawn) to this file")
    args = ap.parse_args()

    cwd = os.path.abspath(args.cwd)
    if args.open:
        s, init_resp = run_open_session(args.server_cmd, cwd, args.timeout, args.root, args.open, args.quiet, args.wait_background, args.full, args.wait_log, args.footprint_trace)
    else:
        s, init_resp = run_session(args.server_cmd, cwd, args.timeout)

    try:
        s.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        s.proc.kill()
        s.proc.wait(timeout=5)
    t_end = time.time()

    # Diagnostics for the 3 didOpen'd files arrive as separate async
    # notifications in receipt order, which depends on clj-kondo's own
    # (:parallel) analysis scheduling -- non-deterministic run-to-run on
    # both JVM and Mova (same flake documented for cli_smoke's same-ns
    # fixture). Sort the printed lines themselves (not just values within
    # one entry) so the golden diff compares the SET of transcript lines,
    # not their arrival order.
    lines = [json.dumps(normalize_value(entry, s.root), sort_keys=True, ensure_ascii=False) for entry in s.transcript]
    for line in sorted(lines):
        print(line)

    rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_rss_mb = rss_kb / 1024 if sys.platform == "darwin" else rss_kb / 1024  # ru_maxrss: bytes on darwin, KB on linux
    if sys.platform != "darwin":
        peak_rss_mb = rss_kb / 1024
    else:
        peak_rss_mb = rss_kb / (1024 * 1024)

    def ms(t):
        return None if t is None else round((t - s.t_spawn) * 1000, 1)

    def mb(b):
        return None if b is None else round(b / (1024 * 1024), 1)

    if args.log_out:
        with open(args.log_out, "w") as f:
            for t, m in s.log_lines:
                f.write("%9.1f %s\n" % ((t - s.t_spawn) * 1000, m.replace("\n", " | ")))
    if args.stderr_out:
        with open(args.stderr_out, "wb") as f:
            f.write(bytes(s.stderr_buf))
    extra = {}
    if args.open:
        extra = {
            "spawn_to_open_file_diagnostics_ms": ms(s.t_open_diag),
            "rss_at_open_file_diagnostics_mb": round(s.rss_at_open_diag_kb / 1024, 1),
            "spawn_to_last_diagnostics_ms": ms(s.t_last_diag),
            "spawn_to_wait_log_ms": ms(getattr(s, "wait_log_hit", None)),
            "published_uris": len(s.diagnostics),
            "rss_at_end_mb": round(getattr(s, "rss_at_end_kb", 0) / 1024, 1),
            "footprint_at_open_file_diagnostics_mb": mb(getattr(s, "fp_open_diag", None)),
            "footprint_at_open_file_lag_ms": round(getattr(s, "fp_open_diag_lag_ms", 0) or 0, 1),
            "peak_footprint_mb": mb(getattr(s, "fp_peak", None)),
            "footprint_at_end_mb": mb(getattr(s, "fp_end", None)),
        }
    print(
        json.dumps(
            {
                **extra,
                "spawn_to_initialize_reply_ms": ms(s.t_init_reply),
                "spawn_to_first_diagnostics_ms": ms(s.t_first_diag),
                "total_ms": ms(t_end),
                "rss_at_initialize_reply_mb": round((s.rss_at_init_kb or 0) / 1024, 1),
                "peak_rss_whole_session_mb": round(peak_rss_mb, 1),
            }
        ),
        file=sys.stderr,
    )
    if not args.root:
        shutil.rmtree(s.root, ignore_errors=True)


if __name__ == "__main__":
    main()
