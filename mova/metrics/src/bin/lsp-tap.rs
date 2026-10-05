//! `lsp-tap [--] <server-cmd> [args...]`: transparent LSP stdio proxy that logs metrics.

use lsp_metrics::tap;
use lsp_metrics::{attrs, sampler, Sink};
use std::fs::File;
use std::mem::ManuallyDrop;
use std::os::fd::FromRawFd;
use std::os::unix::process::ExitStatusExt;
use std::process::{Command, Stdio};
use std::sync::mpsc::sync_channel;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use tap::proxy::{forward, parse_loop, Msg, CLIENT_EXIT};
use tap::state::Dir;

/// Waits for a thread up to `max`; joins it only if it finished.
fn join_bounded(h: JoinHandle<()>, max: Duration) {
    let end = Instant::now() + max;
    while !h.is_finished() && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(2));
    }
    if h.is_finished() {
        let _ = h.join();
    }
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut kind_flag = None;
    if args.first().map(|a| a == "--kind").unwrap_or(false) && args.len() >= 2 {
        kind_flag = Some(args[1].clone());
        args.drain(..2);
    }
    if args.first().map(|a| a == "--").unwrap_or(false) {
        args.remove(0);
    }
    if args.is_empty() {
        eprintln!("usage: lsp-tap [--] <server-cmd> [args...]");
        std::process::exit(2);
    }
    let enabled = std::env::var("LSP_METRICS").map(|v| v != "0").unwrap_or(true);
    let (sink, guard) = Sink::open(None);
    let chan = enabled.then(|| tap::server::start(sink.clone())).flatten();
    let mut cmd = Command::new(&args[0]);
    cmd.args(&args[1..]).stdin(Stdio::piped()).stdout(Stdio::piped());
    if let Some(c) = &chan {
        cmd.env("LSP_METRICS_SOCK", &c.path).env("LSP_METRICS_SESSION", sink.session());
    }
    let sig_fd = tap::signals::install();
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("lsp-tap: cannot run {}: {e}", args[0]);
            if let Some(c) = chan {
                c.close();
            }
            guard.finish(sink);
            std::process::exit(127);
        }
    };
    let pid = child.id();
    let sampler_h = sampler::spawn(pid as i32, sink.clone());
    tap::signals::forward_to(sig_fd, pid as i32);
    let kind = tap::session::server_kind(kind_flag, &args[0]);
    let start_h = tap::session::spawn_start(sink.clone(), args.clone(), pid, kind);
    let (tx, rx) = sync_channel::<Msg>(4096);
    let parser = enabled.then(|| {
        let s = sink.clone();
        std::thread::spawn(move || parse_loop(rx, s))
    });
    let ptx = || enabled.then(|| tx.clone());

    let c_in = child.stdin.take().expect("child stdin");
    let c_out = child.stdout.take().expect("child stdout");
    // Forwarders hold no Sink clone (a blocked stdin thread would stall SinkGuard::finish).
    let clock = (Instant::now(), sink.now_ns());
    let t1 = ptx();
    let eh = enabled.then(|| sink.end_handle());
    // Editor stdin is never joined: it may block forever after the child exits.
    std::thread::spawn(move || {
        let stdin = ManuallyDrop::new(unsafe { File::from_raw_fd(0) });
        forward(Dir::ClientToServer, &*stdin, c_in, t1, clock, eh);
    });
    let t2 = ptx();
    let out_h = std::thread::spawn(move || {
        let stdout = ManuallyDrop::new(unsafe { File::from_raw_fd(1) });
        forward(Dir::ServerToClient, c_out, &*stdout, t2, clock, None);
    });

    let status = child.wait();
    join_bounded(out_h, Duration::from_millis(300));
    if let Some(p) = parser {
        let _ = tx.send(Msg::Stop);
        join_bounded(p, Duration::from_secs(1));
    }
    join_bounded(start_h, Duration::from_millis(500));
    join_bounded(sampler_h, Duration::from_millis(300));

    let (code, a) = match status {
        Ok(s) => match (s.code(), s.signal()) {
            (Some(c), _) => (c, attrs! {"exit.code" => c}),
            (None, Some(sig)) => (128 + sig, attrs! {"exit.signal" => sig}),
            _ => (1, attrs! {}),
        },
        Err(_) => (1, attrs! {}),
    };
    let mut a = a;
    let reason = if CLIENT_EXIT.load(std::sync::atomic::Ordering::SeqCst) { "client-exit" } else { "server-exit" };
    a.insert("end.reason".into(), reason.into());
    let bad = chan.map(|c| c.close()).unwrap_or(0);
    a.insert("server.bad".into(), bad.into());
    a.insert("uptime_ns".into(), sink.now_ns().into());
    a.insert("events.written".into(), sink.written().into());
    a.insert("events.dropped".into(), sink.dropped().into());
    if sink.claim_end() {
        sink.event("tap", "session.end", a);
    }
    drop(tx);
    guard.finish(sink);
    std::process::exit(code);
}
