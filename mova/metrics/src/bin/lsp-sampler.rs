//! Standalone sampler: `lsp-sampler --pid <n> [--session <id>]`.
use lsp_metrics::{sampler, Sink};

fn main() {
    let (mut pid, mut session) = (None, None);
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--pid" => pid = it.next().and_then(|v| v.parse::<i32>().ok()),
            "--session" => session = it.next(),
            _ => {}
        }
    }
    let Some(pid) = pid else {
        eprintln!("usage: lsp-sampler --pid <n> [--session <id>]");
        std::process::exit(2);
    };
    let (sink, guard) = Sink::open(session);
    let _ = sampler::spawn(pid, sink.clone()).join();
    guard.finish(sink);
}
