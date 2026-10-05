//! project.census: one background walk of the workspace root after `initialize`.

use crate::{attrs, file_ext, Sink};
use std::path::PathBuf;

const SKIP: [&str; 8] = [".git", "target", "node_modules", ".cpcache", ".clj-kondo", ".lsp", ".shadow-cljs", "out"];
const MAX_ENTRIES: u64 = 100_000;

pub fn spawn(sink: Sink, root: String) {
    std::thread::spawn(move || run(&sink, &root));
}

fn run(sink: &Sink, root: &str) {
    let t0 = sink.now_ns();
    let (mut clj, mut cljs, mut cljc, mut edn, mut mova, mut other, mut bytes, mut seen) = (0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let mut stack = vec![PathBuf::from(root)];
    'walk: while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        for e in rd.flatten() {
            seen += 1;
            if seen > MAX_ENTRIES {
                break 'walk;
            }
            let Ok(ft) = e.file_type() else { continue };
            let name = e.file_name().to_string_lossy().into_owned();
            if ft.is_dir() {
                if !(SKIP.contains(&name.as_str()) || name == "classes") {
                    stack.push(e.path());
                }
            } else if ft.is_file() {
                let ext = file_ext(&name);
                let counter = match ext {
                    "clj" => &mut clj,
                    "cljs" => &mut cljs,
                    "cljc" => &mut cljc,
                    "edn" => &mut edn,
                    "mova" => &mut mova,
                    _ => &mut other,
                };
                *counter += 1;
                if !matches!(ext, "other" | "bb") {
                    bytes += e.metadata().map(|m| m.len()).unwrap_or(0);
                }
            }
        }
    }
    let has = |f: &str| std::path::Path::new(root).join(f).exists();
    let kind = ["deps.edn", "project.clj", "bb.edn"].into_iter().find(|f| has(f)).map(|f| f.to_string()).unwrap_or_else(|| "none".into());
    sink.event("tap", "project.census", attrs! {
        "files.clj" => clj, "files.cljs" => cljs, "files.cljc" => cljc, "files.edn" => edn,
        "files.mova" => mova, "files.other" => other, "bytes.source" => bytes,
        "deps.kind" => kind, "walk.entries" => seen, "walk.ns" => sink.now_ns() - t0,
    });
}
