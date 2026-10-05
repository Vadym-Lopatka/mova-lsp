#!/usr/bin/env python3
"""lsp/io + lsp/host (clojure-lsp-on-Mova campaign, mova/PLAN.md): drives
a smoke server (unmodified, either the JVM or the Mova run) over real
stdio, exactly like a language client would -- sends framed JSON-RPC
requests/notifications, reads framed responses/notifications back, and
prints a NORMALIZED transcript (one JSON value per line, keys sorted, so
key-order/whitespace differences between cheshire/Jackson on the JVM and
this campaign's serde_json-backed native on Mova never show up as a
spurious diff -- PLAN.md's oracle rule cares about VALUES, not
byte-for-byte JSON text).

Usage:
    python3 drive_jsonrpc.py [--cwd DIR] [--protocol {jsonrpc-echo,lsp4clj}] -- <command...>

    python3 drive_jsonrpc.py --cwd cli -- clojure -M ../mova/smoke/jsonrpc_echo.clj
    python3 drive_jsonrpc.py -- env MOVA_BIN=<mova-bin> mova/bin/lsp-mova mova/smoke/jsonrpc_echo.clj
    python3 drive_jsonrpc.py --protocol lsp4clj --cwd cli -- clojure -M ../mova/smoke/lsp4clj_echo.clj

Two scripts:
  - "jsonrpc-echo" (default): drives `jsonrpc_echo.clj` (this campaign's
    hand-rolled dispatcher over `jsonrpc4clj.io-chan` alone). Pipelines
    every message up front, then collects replies -- that server has no
    server-initiated traffic, so ordering doesn't matter.
  - "lsp4clj": drives `lsp4clj_echo.clj` (the REAL `jsonrpc4clj.server`
    engine). Sends each message and reads its reply IN ORDER (a real LSP
    client never fires `exit` before earlier responses have arrived,
    and this script doesn't either) -- includes a REQUEST from the
    server (`send-request`) that this driver must itself reply to
    before the exchange can continue, exercising the server->client
    direction, not just request/response and notifications.

Includes one non-ASCII payload in the "jsonrpc-echo" script ("héllo ✓")
specifically to prove Content-Length is a BYTE count, not a char count --
a reader that miscounted chars instead of UTF-8 bytes would either hang
(reading too few bytes, waiting for more) or desync the NEXT frame
(reading too many, eating into the following header) -- either failure
mode shows up immediately as a wrong/missing response in this driver's
output.

The driven process is spawned with a timeout (never backgrounded, never
`tail -f`'d -- this script owns the whole lifetime of its one subprocess
and always terminates it before exiting) so a hang in the driven server
can never hang the driver itself.
"""
import json
import subprocess
import sys
import time

TIMEOUT_S = 20.0


def frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_frame(read_byte, deadline: float) -> dict | str | None:
    """Reads one Content-Length-framed JSON-RPC message the same way the
    real LSP base protocol specifies: a run of `Header: value\\r\\n` lines
    (blank line ends them), then exactly Content-Length BYTES of body.
    Returns the parsed dict, the string "eof", or raises TimeoutError.
    """
    headers = {}
    line = bytearray()
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("timed out reading header line")
        b = read_byte()
        if b == b"":
            return "eof"
        if b == b"\n":
            text = line.decode("ascii")
            if text == "":
                break
            if ":" in text:
                k, v = text.split(":", 1)
                headers[k.strip()] = v.strip()
            line = bytearray()
        elif b != b"\r":
            line += b
    n = int(headers["Content-Length"])
    body = bytearray()
    while len(body) < n:
        if time.monotonic() > deadline:
            raise TimeoutError("timed out reading body")
        chunk = read_byte(n - len(body))
        if not chunk:
            return "eof"
        body += chunk
    return json.loads(body.decode("utf-8"))


def normalize(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def run_jsonrpc_echo(proc, read_byte, deadline) -> list[str]:
    transcript: list[str] = []
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        # Non-ASCII proof case: "héllo ✓" is 7 chars, 10 UTF-8 bytes.
        {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"text": "héllo ✓", "n": 42}},
        {"jsonrpc": "2.0", "id": 3, "method": "no/such/method", "params": {}},
        {"jsonrpc": "2.0", "method": "notify/trigger", "params": {"tag": "smoke"}},
    ]
    expected_replies = 4  # 3 request responses + 1 triggered notification
    try:
        for msg in messages:
            proc.stdin.write(frame(msg))
            proc.stdin.flush()
    except BrokenPipeError:
        transcript.append("BROKEN PIPE writing requests (process exited early)")
        return transcript

    for _ in range(expected_replies):
        try:
            reply = read_frame(read_byte, deadline)
        except TimeoutError as e:
            transcript.append(f"TIMEOUT: {e}")
            break
        if reply == "eof":
            transcript.append("EOF")
            break
        transcript.append(normalize(reply))

    try:
        proc.stdin.write(frame({"jsonrpc": "2.0", "method": "exit"}))
        proc.stdin.flush()
        proc.stdin.close()
    except BrokenPipeError:
        pass
    return transcript


def run_lsp4clj(proc, read_byte, deadline) -> list[str]:
    """Drives `lsp4clj_echo.clj`'s real `jsonrpc4clj.server` protocol:
    initialize, echo, a request that throws (-> a real -32603 Internal
    error response, produced by jsonrpc4clj.server itself), a
    notification that triggers a SERVER-INITIATED request (which this
    driver answers, exercising send-request/deref on a real
    PendingRequest) followed by a server notification, then a real
    shutdown/exit sequence -- checks the process actually exits.
    """
    transcript: list[str] = []

    def send(obj):
        proc.stdin.write(frame(obj))
        proc.stdin.flush()

    def recv_raw():
        return read_frame(read_byte, deadline)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        transcript.append(normalize(recv_raw()))

        send({"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"x": 1, "text": "héllo ✓"}})
        transcript.append(normalize(recv_raw()))

        send({"jsonrpc": "2.0", "id": 3, "method": "will-throw", "params": {}})
        transcript.append(normalize(recv_raw()))

        send({"jsonrpc": "2.0", "method": "notify/trigger", "params": {"tag": "smoke"}})
        server_req = recv_raw()
        transcript.append(normalize(server_req))
        if isinstance(server_req, dict) and "id" in server_req:
            send({"jsonrpc": "2.0", "id": server_req["id"], "result": {"pong": True}})
        transcript.append(normalize(recv_raw()))  # the server/notified notification

        send({"jsonrpc": "2.0", "id": 4, "method": "shutdown", "params": {}})
        transcript.append(normalize(recv_raw()))

        send({"jsonrpc": "2.0", "method": "exit"})
        proc.stdin.close()
    except BrokenPipeError:
        transcript.append("BROKEN PIPE (process exited early)")
    except TimeoutError as e:
        transcript.append(f"TIMEOUT: {e}")
    return transcript


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: drive_jsonrpc.py [--cwd DIR] [--protocol {jsonrpc-echo,lsp4clj}] -- <command...>", file=sys.stderr)
        return 2
    split = argv.index("--")
    opts, cmd = argv[:split], argv[split + 1 :]
    cwd = None
    if "--cwd" in opts:
        cwd = opts[opts.index("--cwd") + 1]
    protocol = "jsonrpc-echo"
    if "--protocol" in opts:
        protocol = opts[opts.index("--protocol") + 1]

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def read_byte(n: int = 1) -> bytes:
        return proc.stdout.read(n)

    deadline = time.monotonic() + TIMEOUT_S
    transcript: list[str] = []
    try:
        if protocol == "lsp4clj":
            transcript = run_lsp4clj(proc, read_byte, deadline)
        else:
            transcript = run_jsonrpc_echo(proc, read_byte, deadline)

        try:
            proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            transcript.append("TIMEOUT waiting for process exit")
        else:
            transcript.append(f"EXIT CODE: {proc.returncode}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    for line in transcript:
        print(line)

    stderr_tail = proc.stderr.read().decode("utf-8", "replace")
    if "--verbose" in argv:
        print("--- stderr ---", file=sys.stderr)
        print(stderr_tail, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
