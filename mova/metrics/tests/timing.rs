use lsp_metrics::tap::proxy::{parse_loop, Msg};
use lsp_metrics::tap::state::Dir;
use lsp_metrics::Sink;
use std::sync::mpsc::sync_channel;

fn frame(v: &serde_json::Value) -> Vec<u8> {
    let b = serde_json::to_vec(v).unwrap();
    [format!("Content-Length: {}\r\n\r\n", b.len()).into_bytes(), b].concat()
}

#[test]
fn span_end_is_read_time_not_parse_time() {
    let dir = std::env::temp_dir().join(format!("lsp-tap-unit-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::env::set_var("LSP_METRICS_DIR", &dir);
    let (sink, guard) = Sink::open(None);
    let (tx, rx) = sync_channel(16);
    let req = serde_json::json!({"jsonrpc":"2.0","id":1,"method":"textDocument/hover","params":{}});
    let resp = serde_json::json!({"jsonrpc":"2.0","id":1,"result":[]});
    tx.send(Msg::Chunk { dir: Dir::ClientToServer, ts: 1_000_000, gap: false, data: frame(&req) }).unwrap();
    tx.send(Msg::Chunk { dir: Dir::ServerToClient, ts: 6_000_000, gap: false, data: frame(&resp) }).unwrap();
    tx.send(Msg::Stop).unwrap();
    // Parser starts late: real elapsed time must not leak into the span.
    std::thread::sleep(std::time::Duration::from_millis(150));
    parse_loop(rx, sink.clone());
    guard.finish(sink);
    let f = std::fs::read_dir(&dir).unwrap().next().unwrap().unwrap().path();
    let text = std::fs::read_to_string(f).unwrap();
    let ev = text.lines().map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap()).find(|e| e["name"] == "lsp.request").unwrap();
    assert_eq!(ev["dur_ns"], 5_000_000);
    assert_eq!(ev["t_ns"], 6_000_000);
}
