use serde_json::{json, Value};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};

const TAP: &str = env!("CARGO_BIN_EXE_lsp-tap");

const ECHO: &str = r#"
import sys
i, o = sys.stdin.buffer, sys.stdout.buffer
while True:
    n = None
    hdr = b""
    while True:
        l = i.readline()
        if not l: sys.exit(0)
        hdr += l
        if l == b"\r\n": break
        if l.lower().startswith(b"content-length:"): n = int(l.split(b":")[1])
    body = i.read(n)
    o.write(hdr + body); o.flush()
"#;

const FAKE: &str = r#"
import sys, json
i, o = sys.stdin.buffer, sys.stdout.buffer
def send(m):
    b = json.dumps(m).encode()
    o.write(b"Content-Length: %d\r\n\r\n" % len(b) + b); o.flush()
while True:
    n = None
    while True:
        l = i.readline()
        if not l: sys.exit(0)
        if l == b"\r\n": break
        if l.lower().startswith(b"content-length:"): n = int(l.split(b":")[1])
    m = json.loads(i.read(n))
    if "id" in m and "method" in m:
        r = {"capabilities": {}} if m["method"] == "initialize" else [1, 2, 3]
        send({"jsonrpc": "2.0", "id": m["id"], "result": r})
    elif m.get("method") == "textDocument/didOpen":
        u = m["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": u, "diagnostics": [{"severity": 1}, {"severity": 2}]}})
"#;

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("lsp-tap-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn frame(body: &[u8]) -> Vec<u8> {
    let mut v = format!("Content-Length: {}\r\n\r\n", body.len()).into_bytes();
    v.extend_from_slice(body);
    v
}

/// Runs the tap over `script`, writing `input`; returns (stdout bytes, exit code).
fn run(dir: &PathBuf, script: &str, input: Vec<u8>) -> (Vec<u8>, i32) {
    let py = dir.join("server.py");
    std::fs::write(&py, script).unwrap();
    let mut child = Command::new(TAP)
        .args(["--", "python3", py.to_str().unwrap()])
        .env("LSP_METRICS_DIR", dir.join("m"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let w = std::thread::spawn(move || {
        stdin.write_all(&input).unwrap();
    });
    let mut out = Vec::new();
    child.stdout.take().unwrap().read_to_end(&mut out).unwrap();
    w.join().unwrap();
    let code = child.wait().unwrap().code().unwrap();
    (out, code)
}

fn events(dir: &PathBuf) -> Vec<Value> {
    let m = dir.join("m");
    let f = std::fs::read_dir(&m).unwrap().next().unwrap().unwrap().path();
    std::fs::read_to_string(f).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
}

#[test]
fn byte_identity() {
    let dir = tmp("ident");
    let mut seed = 12345u64;
    let mut rnd = move || {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (seed >> 33) as usize
    };
    let mut input = Vec::new();
    for k in 0..200 {
        let body: Vec<u8> = if k == 100 {
            let s = "x".repeat(1_000_000);
            serde_json::to_vec(&json!({"jsonrpc": "2.0", "method": "big", "params": {"s": s}})).unwrap()
        } else if k % 7 == 0 {
            let s = "héllo wörld ✓ 日本語 🚀".repeat(rnd() % 50 + 1);
            serde_json::to_vec(&json!({"jsonrpc": "2.0", "method": "u", "params": {"s": s}})).unwrap()
        } else {
            let n = rnd() % 5000;
            (0..n).map(|_| b'a' + (rnd() % 26) as u8).collect()
        };
        input.extend(frame(&body));
    }
    let (out, code) = run(&dir, ECHO, input.clone());
    assert_eq!(code, 0);
    assert!(out == input, "output differs ({} vs {} bytes)", out.len(), input.len());
}

#[test]
fn events_logged() {
    let dir = tmp("events");
    let msgs = [
        json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": "file:///tmp/proj", "clientInfo": {"name": "emacs"}}}),
        json!({"jsonrpc": "2.0", "method": "initialized", "params": {}}),
        json!({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": "file:///tmp/proj/src/a%20b.clj", "version": 1, "text": "(ns a)\n(def x 1)\n"}}}),
        json!({"jsonrpc": "2.0", "id": 2, "method": "textDocument/hover", "params": {"textDocument": {"uri": "file:///tmp/proj/src/a%20b.clj"}}}),
    ];
    let mut input = Vec::new();
    for m in &msgs {
        input.extend(frame(&serde_json::to_vec(m).unwrap()));
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    let (_, code) = run(&dir, FAKE, input);
    assert_eq!(code, 0);
    let ev = events(&dir);
    let by = |n: &str| ev.iter().find(|e| e["name"] == n).unwrap_or_else(|| panic!("missing {n}: {ev:?}")).clone();
    assert_eq!(by("session.start")["attrs"]["tap.version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(by("lsp.initialize")["attrs"]["root.path"], "/tmp/proj");
    let req = by("lsp.request");
    assert_eq!(req["attrs"]["method"], "textDocument/hover");
    assert_eq!(req["attrs"]["result.count"], 3);
    assert_eq!(req["attrs"]["file.path"], "src/a b.clj");
    assert_eq!(req["attrs"]["file.origin"], "project");
    assert_eq!(req["attrs"]["file.lines"], 3);
    assert!(req["dur_ns"].is_u64());
    let d = by("lsp.diagnostics");
    assert_eq!(d["attrs"]["trigger"], "didOpen");
    assert_eq!(d["attrs"]["diag.count"], 2);
    assert_eq!(d["attrs"]["doc.version"], 1);
    assert_eq!(by("session.end")["attrs"]["exit.code"], 0);
}

#[test]
fn exit_code() {
    let dir = tmp("exit");
    let (_, code) = run(&dir, "import sys\nsys.exit(3)\n", Vec::new());
    assert_eq!(code, 3);
}

const SRV: &str = r#"
import os, socket, json, sys, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
p = os.environ["LSP_METRICS_SOCK"]
for m in [
  {"kind": "span", "name": "srv.analyze", "dur_ns": 5000000, "attrs": {"files": 1}},
  {"kind": "sample", "name": "mova.runtime", "attrs": {"alloc.count": 7}},
  {"kind": "event", "name": "bogus.name", "attrs": {}},
]:
    s.sendto(json.dumps(m).encode(), p)
    time.sleep(0.05)
time.sleep(0.3)
"#;

#[test]
fn server_channel_and_census() {
    let dir = tmp("srv");
    let root = dir.join("proj");
    std::fs::create_dir_all(root.join("src")).unwrap();
    std::fs::create_dir_all(root.join("target")).unwrap();
    std::fs::write(root.join("src/a.clj"), "(ns a)").unwrap();
    std::fs::write(root.join("src/b.clj"), "(ns b)").unwrap();
    std::fs::write(root.join("deps.edn"), "{}").unwrap();
    std::fs::write(root.join("target/x.clj"), "skipped").unwrap();
    let init = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": format!("file://{}", root.display())}});
    let (_, code) = run(&dir, &format!("{SRV}\n{FAKE}"), frame(&serde_json::to_vec(&init).unwrap()));
    assert_eq!(code, 0);
    let ev = events(&dir);
    let by = |n: &str| ev.iter().find(|e| e["name"] == n).unwrap_or_else(|| panic!("missing {n}: {ev:?}")).clone();
    let sp = by("srv.analyze");
    assert_eq!((sp["src"].as_str(), sp["dur_ns"].as_u64()), (Some("server"), Some(5_000_000)));
    assert_eq!(by("mova.runtime")["attrs"]["alloc.count"], 7);
    assert_eq!(by("session.end")["attrs"]["server.bad"], 1);
    let c = by("project.census")["attrs"].clone();
    assert_eq!((c["files.clj"].as_u64(), c["files.edn"].as_u64(), c["deps.kind"].as_str()), (Some(2), Some(1), Some("deps.edn")));
    assert_eq!(c["files.other"].as_u64(), Some(0));
    assert_eq!(by("session.start")["attrs"]["server.kind"], "jvm");
}

#[test]
fn sigterm_is_forwarded() {
    let dir = tmp("sig");
    let mut child = Command::new(TAP)
        .args(["--kind", "mova", "--", "sleep", "30"])
        .env("LSP_METRICS_DIR", dir.join("m"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(500));
    unsafe { libc::kill(child.id() as i32, libc::SIGTERM) };
    let t0 = std::time::Instant::now();
    let st = child.wait().unwrap();
    assert!(t0.elapsed().as_secs() < 2);
    assert_eq!(st.code(), Some(128 + 15));
    let ev = events(&dir);
    let end = ev.iter().find(|e| e["name"] == "session.end").expect("session.end");
    assert_eq!(end["attrs"]["exit.signal"], 15);
    assert_eq!(ev.iter().find(|e| e["name"] == "session.start").unwrap()["attrs"]["server.kind"], "mova");
}

#[test]
fn sigkill_after_exit_keeps_session_end() {
    let dir = tmp("kill");
    let py = dir.join("server.py");
    let script = format!("import os\nopen(r'{}','w').write(str(os.getpid()))\n{}", dir.join("pid").display(), FAKE);
    std::fs::write(&py, script).unwrap();
    let mut child = Command::new(TAP)
        .args(["--", "python3", py.to_str().unwrap()])
        .env("LSP_METRICS_DIR", dir.join("m"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut out = child.stdout.take().unwrap();
    let mut send = |m: Value| stdin.write_all(&frame(m.to_string().as_bytes())).unwrap();
    send(json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}));
    send(json!({"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":"file:///a.clj","text":""}}}));
    send(json!({"jsonrpc":"2.0","id":2,"method":"shutdown"}));
    let mut got = Vec::new();
    let mut b = [0u8; 4096];
    while !String::from_utf8_lossy(&got).contains("\"id\": 2") {
        let n = out.read(&mut b).unwrap();
        assert!(n > 0);
        got.extend_from_slice(&b[..n]);
    }
    send(json!({"jsonrpc":"2.0","method":"exit"}));
    std::thread::sleep(std::time::Duration::from_millis(5));
    unsafe { libc::kill(child.id() as i32, libc::SIGKILL) };
    let _ = child.wait();
    let ev = events(&dir);
    let ends: Vec<_> = ev.iter().filter(|e| e["name"] == "session.end").collect();
    assert_eq!(ends.len(), 1, "{ev:?}");
    assert_eq!(ends[0]["attrs"]["end.reason"], "client-shutdown");
    for n in ["session.start", "lsp.initialize"] {
        assert!(ev.iter().any(|e| e["name"] == n), "missing {n}");
    }
    assert!(ev.iter().any(|e| e["name"] == "lsp.request" && e["attrs"]["method"] == "shutdown"));
    // Server must exit on stdin EOF (no orphan).
    let pid: i32 = std::fs::read_to_string(dir.join("pid")).unwrap().parse().unwrap();
    let end = std::time::Instant::now() + std::time::Duration::from_secs(2);
    while unsafe { libc::kill(pid, 0) } == 0 && std::time::Instant::now() < end {
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert!(unsafe { libc::kill(pid, 0) } != 0, "server orphaned");
}
