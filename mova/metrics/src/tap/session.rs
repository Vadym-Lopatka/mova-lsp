//! session.start: gathers build info off the hot path (git calls run in a background thread).

use crate::{attrs, Sink};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread::JoinHandle;

fn git_rev(dir: &Path) -> Option<String> {
    let dir = if dir.as_os_str().is_empty() { Path::new(".") } else { dir };
    let out = Command::new("git").arg("-C").arg(dir).args(["rev-parse", "--short", "HEAD"]).stderr(Stdio::null()).output().ok()?;
    let s = String::from_utf8(out.stdout).ok()?.trim().to_string();
    if out.status.success() && !s.is_empty() { Some(s) } else { None }
}

fn resolve(cmd: &str) -> Option<PathBuf> {
    if cmd.contains('/') {
        return Some(PathBuf::from(cmd));
    }
    std::env::split_paths(&std::env::var_os("PATH")?).map(|d| d.join(cmd)).find(|p| p.is_file())
}

pub fn spawn_start(sink: Sink, cmd: Vec<String>, pid: u32, kind: String) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mova_bin = std::env::var("MOVA_BIN").ok();
        let mova_rev = mova_bin.as_ref().and_then(|b| git_rev(Path::new(b).parent().unwrap_or(Path::new("."))));
        let lsp_rev = resolve(&cmd[0]).and_then(|p| git_rev(p.parent().unwrap_or(Path::new("."))));
        let cwd = std::env::current_dir().ok().map(|p| p.display().to_string());
        let env = |k: &str| std::env::var(k).ok();
        sink.event("tap", "session.start", attrs! {
            "server.cmd" => cmd.join(" "),
            "server.pid" => pid,
            "server.kind" => kind,
            "cwd" => cwd,
            "build.mova_bin" => mova_bin,
            "build.mova_rev" => mova_rev,
            "build.lsp_rev" => lsp_rev,
            "env.MOVA_JIT" => env("MOVA_JIT"),
            "env.MOVA_IMAGE" => env("MOVA_IMAGE"),
            "env.MOVA_SHARDS" => env("MOVA_SHARDS"),
            "tap.version" => env!("CARGO_PKG_VERSION"),
        });
    })
}

/// Server kind: explicit flag, else "mova" when the command path mentions mova or MOVA_BIN is set.
pub fn server_kind(flag: Option<String>, cmd: &str) -> String {
    flag.unwrap_or_else(|| if cmd.contains("mova") || std::env::var_os("MOVA_BIN").is_some() { "mova" } else { "jvm" }.to_string())
}
