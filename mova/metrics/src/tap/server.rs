//! Server channel: Unix datagram socket receiving in-process events from the server (SCHEMA.md).

use crate::{Attrs, Sink};
use serde_json::Value;
use std::os::unix::net::UnixDatagram;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

pub struct Channel {
    pub path: PathBuf,
    pub bad: Arc<AtomicU64>,
    stop: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

/// Socket path for a session: `$TMPDIR/lsp-metrics-<session>.sock`, `/tmp` if too long.
pub fn sock_path(session: &str) -> PathBuf {
    let name = format!("lsp-metrics-{session}.sock");
    let p = std::env::temp_dir().join(&name);
    if p.as_os_str().len() > 100 { PathBuf::from("/tmp").join(name) } else { p }
}

/// Parses one datagram and emits it with end time `end`; returns false when it is bad.
pub fn ingest(sink: &Sink, data: &[u8], end: u64) -> bool {
    let Ok(Value::Object(mut m)) = serde_json::from_slice::<Value>(data) else { return false };
    let name = match m.get("name").and_then(|n| n.as_str()) {
        Some(n) if n.starts_with("srv.") || n.starts_with("mova.") => n.to_string(),
        _ => return false,
    };
    let mut attrs = match m.remove("attrs") {
        Some(Value::Object(a)) => a,
        None => Attrs::new(),
        _ => return false,
    };
    if let Some(t) = m.remove("thread") {
        attrs.insert("thread".into(), t);
    }
    match m.get("kind").and_then(|k| k.as_str()) {
        Some("span") => {
            let Some(dur) = m.get("dur_ns").and_then(|d| d.as_u64()) else { return false };
            sink.span_at("server", name, end.saturating_sub(dur), end, attrs);
        }
        Some("event") => sink.event_at("server", name, end, attrs),
        Some("sample") => sink.sample("server", name, attrs),
        _ => return false,
    }
    true
}

/// Binds the socket and starts the receiver thread. None if binding fails.
pub fn start(sink: Sink) -> Option<Channel> {
    let path = sock_path(sink.session());
    let _ = std::fs::remove_file(&path);
    let sock = UnixDatagram::bind(&path).ok()?;
    sock.set_read_timeout(Some(Duration::from_millis(100))).ok()?;
    let bad = Arc::new(AtomicU64::new(0));
    let stop = Arc::new(AtomicBool::new(false));
    let (b, s) = (bad.clone(), stop.clone());
    let handle = std::thread::spawn(move || {
        let mut buf = vec![0u8; 16 * 1024];
        while !s.load(Ordering::Relaxed) {
            match sock.recv(&mut buf) {
                Ok(n) => {
                    let end = sink.now_ns();
                    if !ingest(&sink, &buf[..n], end) {
                        b.fetch_add(1, Ordering::Relaxed);
                    }
                }
                Err(_) => {}
            }
        }
    });
    Some(Channel { path, bad, stop, handle })
}

impl Channel {
    /// Stops the receiver (drains nothing more), removes the socket file, returns the bad count.
    pub fn close(self) -> u64 {
        self.stop.store(true, Ordering::Relaxed);
        let _ = self.handle.join();
        let _ = std::fs::remove_file(&self.path);
        self.bad.load(Ordering::Relaxed)
    }
}
