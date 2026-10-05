//! Forwarding threads (bytes first, never wait on parsing) and the parser thread.

use super::framing::Framer;
use super::state::{Dir, Tracker};
use std::time::Instant;
use std::io::{ErrorKind, Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use crate::{attrs, EndHandle};
use std::sync::mpsc::{Receiver, SyncSender, TrySendError};

pub enum Msg {
    Chunk { dir: Dir, ts: u64, gap: bool, data: Vec<u8> },
    Stop,
}

/// Set when the client's `exit` notification was seen (decides `end.reason`).
pub static CLIENT_EXIT: AtomicBool = AtomicBool::new(false);

/// True when `chunk` holds `"method": "<name>"` (cheap scan, no JSON parse).
fn has_method(chunk: &[u8], name: &str) -> bool {
    let key = b"\"method\"";
    let mut from = 0;
    while let Some(i) = chunk[from..].windows(key.len()).position(|w| w == key) {
        let mut j = from + i + key.len();
        while j < chunk.len() && matches!(chunk[j], b' ' | b':' | b'\t') {
            j += 1;
        }
        let want = format!("\"{name}\"");
        if chunk[j..].starts_with(want.as_bytes()) {
            return true;
        }
        from += i + key.len();
    }
    false
}

/// Session end attrs for the client-exit path.
pub fn end_attrs(reason: &str) -> crate::Attrs {
    attrs! {"end.reason" => reason}
}

/// Copies `src` to `dst` chunk by chunk; a copy goes to the parser via try_send (dropped when full).
pub fn forward<R: Read, W: Write>(dir: Dir, mut src: R, mut dst: W, tx: Option<SyncSender<Msg>>, clock: (Instant, u64), end: Option<EndHandle>) {
    let mut buf = vec![0u8; 64 * 1024];
    let mut gap = false;
    loop {
        let n = match src.read(&mut buf) {
            Ok(0) => return,
            Ok(n) => n,
            Err(e) if e.kind() == ErrorKind::Interrupted => continue,
            Err(_) => return,
        };
        let (is_exit, is_shutdown) = match &end {
            Some(_) => (has_method(&buf[..n], "exit"), has_method(&buf[..n], "shutdown")),
            None => (false, false),
        };
        if is_exit {
            CLIENT_EXIT.store(true, Ordering::SeqCst);
        }
        if dst.write_all(&buf[..n]).and_then(|_| dst.flush()).is_err() {
            return;
        }
        if let (Some(h), true) = (&end, is_exit || is_shutdown) {
            // lsp-mode sends `exit` and SIGKILLs the tap at once, often before `exit` is even read
            // (seen in real Emacs): end the session at `shutdown` (only `exit` may follow it), end
            // line first (one direct write, no waiting), then flush the queued events.
            if h.claim_end() {
                h.append_now("tap", "session.end", end_attrs(if is_exit { "client-exit" } else { "client-shutdown" }));
            }
            h.flush_sync(Duration::from_millis(50));
        }
        let Some(tx) = &tx else { continue };
        let msg = Msg::Chunk { dir, ts: clock.1 + clock.0.elapsed().as_nanos() as u64, gap, data: buf[..n].to_vec() };
        match tx.try_send(msg) {
            Ok(()) => gap = false,
            Err(TrySendError::Full(_)) => gap = true,
            Err(TrySendError::Disconnected(_)) => gap = false,
        }
    }
}

/// Parser thread body: frames both directions and feeds the tracker until `Stop`.
pub fn parse_loop(rx: Receiver<Msg>, sink: crate::Sink) {
    let mut tracker = Tracker::new(sink);
    let mut framers = [Framer::new(), Framer::new()];
    let mut out: Vec<Vec<u8>> = Vec::new();
    while let Ok(msg) = rx.recv() {
        let Msg::Chunk { dir, ts, gap, data } = msg else { return };
        let f = &mut framers[if dir == Dir::ClientToServer { 0 } else { 1 }];
        for _ in 0..f.feed(&data, gap, &mut out) {
            tracker.resync(dir, ts);
        }
        for body in out.drain(..) {
            tracker.handle(dir, ts, &body);
        }
    }
}
