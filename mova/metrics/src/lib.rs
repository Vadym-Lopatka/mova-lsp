//! Shared core for lsp-metrics: event shape (see SCHEMA.md) and a non-blocking file sink.
//! Producers call `Sink::emit`; it never blocks and never fails — when the writer
//! thread falls behind, events are dropped and counted.

use serde::Serialize;
use serde_json::{Map, Value};
use std::borrow::Cow;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub mod sampler;
pub mod tap;

pub const SCHEMA_VERSION: u32 = 1;
const QUEUE: usize = 8192;
const FLUSH_BYTES: usize = 64 * 1024;
const FLUSH_EVERY: Duration = Duration::from_millis(500);

pub type Attrs = Map<String, Value>;

#[derive(Serialize)]
pub struct Event {
    pub v: u32,
    pub ts_ms: u64,
    pub t_ns: u64,
    pub session: Arc<str>,
    pub src: &'static str,
    pub kind: &'static str,
    pub name: Cow<'static, str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dur_ns: Option<u64>,
    pub attrs: Attrs,
}

/// Build attrs inline: `attrs! {"method" => m, "id" => 3}`. Null values are skipped.
#[macro_export]
macro_rules! attrs {
    ($($k:expr => $v:expr),* $(,)?) => {{
        let mut m = $crate::Attrs::new();
        $( let v = serde_json::json!($v); if !v.is_null() { m.insert($k.to_string(), v); } )*
        m
    }};
}

enum Cmd {
    Ev(Event),
    Flush,
}

struct Shared {
    written: AtomicU64,
    dropped: AtomicU64,
    /// Flush requests issued / completed (writer bumps `done` under the mutex).
    flush_req: AtomicU64,
    flush_done: Mutex<u64>,
    flush_cv: Condvar,
    /// Wakes the writer for flush_sync; taken (dropped) by `finish` so the writer can end.
    flush_tx: Mutex<Option<SyncSender<Cmd>>>,
    /// Set once by whoever writes `session.end`.
    ended: AtomicBool,
}

/// Client-exit hook for forwarder threads: holds no queue sender that could stall `finish`.
#[derive(Clone)]
pub struct EndHandle {
    session: Arc<str>,
    t0: Instant,
    shared: Arc<Shared>,
}

impl EndHandle {
    /// Asks the writer to flush all queued events; waits at most `timeout`. True when done.
    pub fn flush_sync(&self, timeout: Duration) -> bool {
        let req = self.shared.flush_req.fetch_add(1, Ordering::SeqCst) + 1;
        if let Some(tx) = self.shared.flush_tx.lock().unwrap().as_ref() {
            let _ = tx.try_send(Cmd::Flush);
        } else {
            return false;
        }
        let g = self.shared.flush_done.lock().unwrap();
        let (g, _) = self.shared.flush_cv.wait_timeout_while(g, timeout, |d| *d < req).unwrap();
        *g >= req
    }

    /// True for the first caller only: one `session.end` per session.
    pub fn claim_end(&self) -> bool {
        !self.shared.ended.swap(true, Ordering::SeqCst)
    }

    /// Appends one event straight to the day file (one write() on an O_APPEND fd), bypassing the queue.
    pub fn append_now(&self, src: &'static str, name: &'static str, mut attrs: Attrs) {
        if std::env::var("LSP_METRICS").map(|v| v == "0").unwrap_or(false) {
            return;
        }
        attrs.insert("uptime_ns".into(), (self.t0.elapsed().as_nanos() as u64).into());
        attrs.insert("events.written".into(), self.shared.written.load(Ordering::Relaxed).into());
        attrs.insert("events.dropped".into(), self.shared.dropped.load(Ordering::Relaxed).into());
        let t_ns = self.t0.elapsed().as_nanos() as u64;
        let ev = Event { v: SCHEMA_VERSION, ts_ms: unix_ms(), t_ns, session: self.session.clone(), src, kind: "event", name: name.into(), dur_ns: None, attrs };
        if let Ok(mut line) = serde_json::to_vec(&ev) {
            line.push(b'\n');
            if let Some(mut f) = open_day_file(&utc_day(ev.ts_ms)) {
                let _ = f.write_all(&line);
            }
        }
    }
}

/// Cheap to clone; all clones feed one writer thread.
#[derive(Clone)]
pub struct Sink {
    tx: Option<SyncSender<Cmd>>,
    session: Arc<str>,
    t0: Instant,
    shared: Arc<Shared>,
}

pub struct SinkGuard {
    handle: Option<JoinHandle<()>>,
}

impl Sink {
    /// Opens the sink. `LSP_METRICS=0` gives a disabled sink (emit is a no-op).
    /// `session`: reuse an id (sampler started by the tap) or None for a new one.
    pub fn open(session: Option<String>) -> (Sink, SinkGuard) {
        let session: Arc<str> = session.unwrap_or_else(new_session_id).into();
        let shared = Arc::new(Shared {
            written: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
            flush_req: AtomicU64::new(0),
            flush_done: Mutex::new(0),
            flush_cv: Condvar::new(),
            flush_tx: Mutex::new(None),
            ended: AtomicBool::new(false),
        });
        let enabled = std::env::var("LSP_METRICS").map(|v| v != "0").unwrap_or(true);
        if !enabled {
            return (Sink { tx: None, session, t0: Instant::now(), shared }, SinkGuard { handle: None });
        }
        let (tx, rx) = sync_channel(QUEUE);
        *shared.flush_tx.lock().unwrap() = Some(tx.clone());
        let sh = shared.clone();
        let handle = std::thread::Builder::new()
            .name("metrics-writer".into())
            .spawn(move || writer_loop(rx, sh))
            .ok();
        (Sink { tx: Some(tx), session, t0: Instant::now(), shared }, SinkGuard { handle })
    }

    pub fn end_handle(&self) -> EndHandle {
        EndHandle { session: self.session.clone(), t0: self.t0, shared: self.shared.clone() }
    }

    /// True for the first caller only (see `EndHandle::claim_end`).
    pub fn claim_end(&self) -> bool {
        !self.shared.ended.swap(true, Ordering::SeqCst)
    }

    pub fn session(&self) -> &str {
        &self.session
    }

    /// ns since this sink was opened (session clock).
    pub fn now_ns(&self) -> u64 {
        self.t0.elapsed().as_nanos() as u64
    }

    pub fn written(&self) -> u64 {
        self.shared.written.load(Ordering::Relaxed)
    }

    pub fn dropped(&self) -> u64 {
        self.shared.dropped.load(Ordering::Relaxed)
    }

    pub fn event(&self, src: &'static str, name: impl Into<Cow<'static, str>>, attrs: Attrs) {
        self.emit(src, "event", name.into(), None, self.now_ns(), attrs)
    }

    /// Event that happened at session time `t_ns` (e.g. when its bytes were read).
    pub fn event_at(&self, src: &'static str, name: impl Into<Cow<'static, str>>, t_ns: u64, attrs: Attrs) {
        self.emit(src, "event", name.into(), None, t_ns, attrs)
    }

    pub fn sample(&self, src: &'static str, name: impl Into<Cow<'static, str>>, attrs: Attrs) {
        self.emit(src, "sample", name.into(), None, self.now_ns(), attrs)
    }

    /// Span that started at session time `start_ns` and ends now.
    pub fn span(&self, src: &'static str, name: impl Into<Cow<'static, str>>, start_ns: u64, attrs: Attrs) {
        self.span_at(src, name, start_ns, self.now_ns(), attrs)
    }

    /// Span with explicit session-time bounds; use when the end was observed earlier than now
    /// (a parser thread lagging behind the byte stream must not stretch durations).
    pub fn span_at(&self, src: &'static str, name: impl Into<Cow<'static, str>>, start_ns: u64, end_ns: u64, attrs: Attrs) {
        self.emit(src, "span", name.into(), Some(end_ns.saturating_sub(start_ns)), end_ns, attrs)
    }

    fn emit(&self, src: &'static str, kind: &'static str, name: Cow<'static, str>, dur_ns: Option<u64>, t_ns: u64, attrs: Attrs) {
        let Some(tx) = &self.tx else { return };
        let ev = Event { v: SCHEMA_VERSION, ts_ms: unix_ms(), t_ns, session: self.session.clone(), src, kind, name, dur_ns, attrs };
        match tx.try_send(Cmd::Ev(ev)) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {
                self.shared.dropped.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

impl SinkGuard {
    /// Flush and stop the writer. Call after the last emit (drop all Sink clones first,
    /// or the writer waits for them; `finish` gives up after 2 s).
    pub fn finish(mut self, sink: Sink) {
        *sink.shared.flush_tx.lock().unwrap() = None;
        drop(sink);
        if let Some(h) = self.handle.take() {
            let deadline = Instant::now() + Duration::from_secs(2);
            while !h.is_finished() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(10));
            }
            if h.is_finished() {
                let _ = h.join();
            }
        }
    }
}

fn writer_loop(rx: Receiver<Cmd>, shared: Arc<Shared>) {
    let mut buf: Vec<u8> = Vec::with_capacity(FLUSH_BYTES * 2);
    let mut n_buf = 0u64;
    let mut file: Option<(String, File)> = None;
    let mut last_flush = Instant::now();
    let mut flushed = 0u64;
    loop {
        let msg = rx.recv_timeout(FLUSH_EVERY);
        let done = matches!(msg, Err(RecvTimeoutError::Disconnected));
        let add = |ev: Event, buf: &mut Vec<u8>, n_buf: &mut u64| {
            if serde_json::to_writer(&mut *buf, &ev).is_ok() {
                buf.push(b'\n');
                *n_buf += 1;
            }
        };
        if let Ok(Cmd::Ev(ev)) = msg {
            add(ev, &mut buf, &mut n_buf);
        }
        let req = shared.flush_req.load(Ordering::SeqCst);
        let forced = req > flushed;
        if forced {
            while let Ok(m) = rx.try_recv() {
                if let Cmd::Ev(ev) = m {
                    add(ev, &mut buf, &mut n_buf);
                }
            }
        }
        if !buf.is_empty() && (forced || done || buf.len() >= FLUSH_BYTES || last_flush.elapsed() >= FLUSH_EVERY) {
            let day = utc_day(unix_ms());
            if file.as_ref().map(|(d, _)| d != &day).unwrap_or(true) {
                file = open_day_file(&day).map(|f| (day, f));
            }
            // One write() per flush of whole lines: safe with O_APPEND and concurrent sessions.
            match file.as_mut().map(|(_, f)| f.write_all(&buf)) {
                Some(Ok(())) => shared.written.fetch_add(n_buf, Ordering::Relaxed),
                _ => shared.dropped.fetch_add(n_buf, Ordering::Relaxed),
            };
            buf.clear();
            n_buf = 0;
            last_flush = Instant::now();
        }
        if forced {
            flushed = req;
            *shared.flush_done.lock().unwrap() = req;
            shared.flush_cv.notify_all();
        }
        if done {
            return;
        }
    }
}

pub fn metrics_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("LSP_METRICS_DIR") {
        return PathBuf::from(d);
    }
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"));
    home.join(".local/state/mova-lsp-metrics")
}

fn open_day_file(day: &str) -> Option<File> {
    let dir = metrics_dir();
    std::fs::create_dir_all(&dir).ok()?;
    OpenOptions::new().create(true).append(true).open(dir.join(format!("events-{day}.jsonl"))).ok()
}

pub fn unix_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// `YYYY-MM-DD` in UTC for a Unix ms timestamp (civil-from-days, no deps).
pub fn utc_day(ms: u64) -> String {
    let z = (ms / 86_400_000) as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + if m <= 2 { 1 } else { 0 };
    format!("{y:04}-{m:02}-{d:02}")
}

pub fn new_session_id() -> String {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    format!("{:x}-{:x}", (nanos / 1_000_000) as u64, (std::process::id() as u64) ^ (nanos as u64 & 0xffff))
}

/// File classification shared by all producers (SCHEMA.md "Common attrs").
pub fn file_ext(path: &str) -> &'static str {
    let ext = path.rsplit('.').next().unwrap_or("");
    match ext {
        "clj" => "clj",
        "cljs" => "cljs",
        "cljc" => "cljc",
        "edn" => "edn",
        "mova" => "mova",
        "bb" => "bb",
        _ => "other",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn day() {
        assert_eq!(utc_day(0), "1970-01-01");
        assert_eq!(utc_day(1_790_000_000_000), "2026-09-21");
        assert_eq!(utc_day(951_782_400_000), "2000-02-29");
    }

    #[test]
    fn attrs_skip_null() {
        let none: Option<u32> = None;
        let a = attrs! {"a" => 1, "b" => none, "c" => "x"};
        assert_eq!(a.len(), 2);
    }

    #[test]
    fn writes_lines() {
        let dir = std::env::temp_dir().join(format!("lsp-metrics-test-{}", std::process::id()));
        std::env::set_var("LSP_METRICS_DIR", &dir);
        let (sink, guard) = Sink::open(Some("t".into()));
        sink.event("tap", "session.start", attrs! {"x" => 1});
        sink.span("tap", "lsp.request", 0, attrs! {"method" => "textDocument/hover"});
        guard.finish(sink);
        let f = dir.join(format!("events-{}.jsonl", utc_day(unix_ms())));
        let s = std::fs::read_to_string(f).unwrap();
        let lines: Vec<Value> = s.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[1]["kind"], "span");
        assert!(lines[1]["dur_ns"].is_u64());
        let _ = std::fs::remove_dir_all(dir);
    }
}
