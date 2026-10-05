"""Tiny stdio LSP client for the nx e2e tests (MOVA_BIN, XDG_CACHE_HOME from env)."""
import json, os, re, subprocess, threading, time
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import nxenv  # private XDG_STATE_HOME / XDG_CONFIG_HOME for every spawned server

NX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "nx-lsp")


def frame(o):
    b = json.dumps(o).encode()
    return b"Content-Length: %d\r\n\r\n" % len(b) + b


class C:
    def __init__(self, cmd=None, cwd=None):
        self.p = subprocess.Popen(cmd or [NX], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.environ.get("NXERR", os.devnull), "w"), cwd=cwd, bufsize=0)
        self.id, self.resp, self.diags, self.pubs, self.last = 0, {}, {}, [], time.time()
        self.reqs = []  # server -> client requests
        self.lock, self.ev = threading.Lock(), threading.Condition()
        threading.Thread(target=self._rd, daemon=True).start()

    def _rd(self):
        buf, fd = b"", self.p.stdout.fileno()
        while True:
            c = os.read(fd, 1 << 16)
            if not c:
                break
            buf += c
            while True:
                i = buf.find(b"\r\n\r\n")
                if i < 0:
                    break
                n = int(re.search(rb"Content-Length: (\d+)", buf[:i]).group(1))
                if len(buf) < i + 4 + n:
                    break
                m = json.loads(buf[i + 4:i + 4 + n])
                buf = buf[i + 4 + n:]
                self._h(m)

    def _h(self, m):
        with self.ev:
            self.last = time.time()
            if "method" in m:
                if "id" in m:
                    self.reqs.append(m)
                    r = [None] * len(m["params"]["items"]) if m["method"] == "workspace/configuration" else None
                    self.w({"jsonrpc": "2.0", "id": m["id"], "result": r})
                elif m["method"] == "textDocument/publishDiagnostics":
                    self.diags[m["params"]["uri"]] = m["params"]["diagnostics"]
                    self.pubs.append((m["params"]["uri"], m["params"]["diagnostics"]))
            else:
                self.resp[m["id"]] = m
            self.ev.notify_all()

    def w(self, o):
        with self.lock:
            try:
                self.p.stdin.write(frame(o))
            except OSError:
                pass

    def notify(self, m, p):
        self.w({"jsonrpc": "2.0", "method": m, "params": p})

    def send(self, m, p):
        self.id += 1
        self.w({"jsonrpc": "2.0", "id": self.id, "method": m, "params": p})
        return self.id

    def req(self, m, p, t=30):
        i = self.send(m, p)
        end = time.time() + t
        with self.ev:
            while i not in self.resp:
                if end - time.time() <= 0:
                    return None
                self.ev.wait(end - time.time())
            return self.resp[i]

    def init(self, root, workspace=None):
        caps = {"textDocument": {"publishDiagnostics": {}}}
        if workspace:
            caps["workspace"] = workspace
        r = self.req("initialize", {"processId": os.getpid(), "rootUri": "file://" + root, "capabilities": caps}, 120)
        self.notify("initialized", {})
        return r

    def quiet(self, q=1.0, maxw=60):
        end = time.time() + maxw
        while time.time() < end:
            if time.time() - self.last > q:
                return True
            time.sleep(0.1)
        return False

    def wait_diag(self, uri, pred, t=10):
        end = time.time() + t
        with self.ev:
            while not pred(self.diags.get(uri)):
                if end - time.time() <= 0:
                    return False
                self.ev.wait(min(0.2, end - time.time()))
        return True

    def close(self):
        try:
            self.req("shutdown", None, 10)
            self.notify("exit", None)
            self.p.wait(10)
        except Exception:
            self.p.kill()


def uri(p):
    return "file://" + p
