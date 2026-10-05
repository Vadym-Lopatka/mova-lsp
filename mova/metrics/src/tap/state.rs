//! Message correlation: turns parsed LSP messages into metrics events (SCHEMA.md).

use crate::{attrs, file_ext, Attrs, Sink};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Clone, Copy, PartialEq)]
pub enum Dir {
    ClientToServer,
    ServerToClient,
}

struct Req {
    method: String,
    id: Value,
    start: u64,
    attrs: Attrs,
    cancelled: bool,
}

struct Doc {
    bytes: u64,
    lines: u64,
}

struct Trigger {
    method: String,
    start: u64,
    version: Option<i64>,
}

pub struct Tracker {
    sink: Sink,
    /// Session-clock read time of the chunk being handled (span/event end).
    now: u64,
    root: Option<String>,
    reqs: HashMap<String, Req>,
    sreqs: HashMap<String, Req>,
    docs: HashMap<String, Doc>,
    pending: HashMap<String, Trigger>,
    progress: HashMap<String, (u64, Option<String>)>,
}

/// Percent-decodes a string (invalid escapes are kept as is).
pub fn pct_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Path part of a URI: `file:///a%20b` -> `/a b`; other schemes: text after `scheme:`.
fn uri_path(uri: &str) -> String {
    let rest = if let Some(r) = uri.strip_prefix("file://") {
        r.find('/').map(|i| &r[i..]).unwrap_or(r)
    } else {
        uri.split_once(':').map(|(_, r)| r).unwrap_or(uri)
    };
    pct_decode(rest)
}

fn is_jar(uri: &str, path: &str) -> bool {
    uri.starts_with("jar:") || uri.starts_with("zipfile:") || path.contains(".jar!") || path.contains(".jar:")
}

fn text_stats(t: &str) -> Doc {
    let nl = t.bytes().filter(|&b| b == b'\n').count() as u64;
    Doc { bytes: t.len() as u64, lines: if t.is_empty() { 0 } else { nl + 1 } }
}

fn count_of(result: &Value) -> Option<u64> {
    match result {
        Value::Array(a) => Some(a.len() as u64),
        Value::Object(o) => o.get("items").and_then(|i| i.as_array()).map(|a| a.len() as u64),
        _ => None,
    }
}

fn key(id: &Value) -> String {
    id.to_string()
}

impl Tracker {
    pub fn new(sink: Sink) -> Self {
        Tracker { sink, now: 0, root: None, reqs: HashMap::new(), sreqs: HashMap::new(), docs: HashMap::new(), pending: HashMap::new(), progress: HashMap::new() }
    }

    pub fn resync(&mut self, dir: Dir, ts_ns: u64) {
        self.now = ts_ns;
        let d = if dir == Dir::ClientToServer { "c2s" } else { "s2c" };
        self.sink.event_at("tap", "tap.resync", self.now, attrs! {"dir" => d});
    }

    fn file_attrs(&self, params: &Value) -> Attrs {
        let mut a = Attrs::new();
        let Some(uri) = params.pointer("/textDocument/uri").and_then(|u| u.as_str()) else { return a };
        let path = uri_path(uri);
        let origin = if is_jar(uri, &path) {
            "jar"
        } else if self.root.as_ref().map(|r| path == *r || path.starts_with(&format!("{r}/"))).unwrap_or(false) {
            "project"
        } else {
            "external"
        };
        let shown = if origin == "project" {
            let r = self.root.as_ref().map(|r| r.len() + 1).unwrap_or(0);
            path.get(r..).unwrap_or(&path).to_string()
        } else {
            path.clone()
        };
        a.insert("file.uri".into(), uri.into());
        a.insert("file.ext".into(), file_ext(&path).into());
        a.insert("file.origin".into(), origin.into());
        if let Some(d) = self.docs.get(uri) {
            a.insert("file.bytes".into(), d.bytes.into());
            a.insert("file.lines".into(), d.lines.into());
        } else if origin != "jar" && uri.starts_with("file:") {
            if let Ok(m) = std::fs::metadata(&path) {
                a.insert("file.bytes".into(), m.len().into());
            }
        }
        a.insert("file.path".into(), shown.into());
        a
    }

    pub fn handle(&mut self, dir: Dir, ts_ns: u64, body: &[u8]) {
        self.now = ts_ns;
        let Ok(msg) = serde_json::from_slice::<Value>(body) else { return };
        let method = msg.get("method").and_then(|m| m.as_str());
        let id = msg.get("id").filter(|i| !i.is_null());
        match (method, id) {
            (Some(m), Some(id)) => self.request(dir, ts_ns, m, id, &msg),
            (Some(m), None) => match dir {
                Dir::ClientToServer => self.client_notify(ts_ns, m, &msg),
                Dir::ServerToClient => self.server_notify(m, &msg),
            },
            (None, Some(id)) => self.response(dir, id, &msg, body.len()),
            _ => {}
        }
    }

    fn request(&mut self, dir: Dir, ts_ns: u64, method: &str, id: &Value, msg: &Value) {
        let params = msg.get("params").unwrap_or(&Value::Null);
        let mut attrs = Attrs::new();
        if dir == Dir::ClientToServer {
            attrs = self.file_attrs(params);
            if method == "initialize" {
                self.set_root(params);
                if let Some(r) = &self.root {
                    attrs.insert("root.path".into(), r.as_str().into());
                }
                if let Some(n) = params.pointer("/clientInfo/name").and_then(|n| n.as_str()) {
                    attrs.insert("client.name".into(), n.into());
                }
            }
        }
        let req = Req { method: method.to_string(), id: id.clone(), start: ts_ns, attrs, cancelled: false };
        let map = if dir == Dir::ClientToServer { &mut self.reqs } else { &mut self.sreqs };
        map.insert(key(id), req);
    }

    fn set_root(&mut self, p: &Value) {
        let uri = p.get("rootUri").and_then(|u| u.as_str()).or_else(|| p.pointer("/workspaceFolders/0/uri").and_then(|u| u.as_str()));
        let root = match uri {
            Some(u) => Some(uri_path(u)),
            None => p.get("rootPath").and_then(|r| r.as_str()).map(|s| s.to_string()),
        };
        self.root = root.map(|r| if r.len() > 1 { r.trim_end_matches('/').to_string() } else { r });
    }

    fn response(&mut self, dir: Dir, id: &Value, msg: &Value, bytes: usize) {
        // A response travelling server->client answers a client request, and vice versa.
        let (map, name) = if dir == Dir::ServerToClient { (&mut self.reqs, "lsp.request") } else { (&mut self.sreqs, "lsp.server_request") };
        let Some(req) = map.remove(&key(id)) else { return };
        let mut a = req.attrs;
        let name = if req.method == "initialize" && dir == Dir::ServerToClient { "lsp.initialize" } else { name };
        if name != "lsp.initialize" {
            a.insert("method".into(), req.method.as_str().into());
            a.insert("id".into(), req.id);
        }
        if dir == Dir::ServerToClient {
            a.insert("resp.bytes".into(), bytes.into());
            if let Some(c) = msg.get("result").and_then(count_of) {
                a.insert("result.count".into(), c.into());
            }
            if let Some(c) = msg.pointer("/error/code") {
                a.insert("error.code".into(), c.clone());
            }
            if req.cancelled {
                a.insert("cancelled".into(), true.into());
            }
        }
        self.sink.span_at("tap", name, req.start, self.now, a);
        if name == "lsp.initialize" {
            if let Some(r) = &self.root {
                super::census::spawn(self.sink.clone(), r.clone());
            }
        }
    }

    fn client_notify(&mut self, ts_ns: u64, method: &str, msg: &Value) {
        let params = msg.get("params").unwrap_or(&Value::Null);
        if method == "$/cancelRequest" {
            if let Some(r) = params.get("id").and_then(|i| self.reqs.get_mut(&key(i))) {
                r.cancelled = true;
            }
            return;
        }
        let uri = params.pointer("/textDocument/uri").and_then(|u| u.as_str()).map(|s| s.to_string());
        let version = params.pointer("/textDocument/version").and_then(|v| v.as_i64());
        let mut change_bytes: Option<u64> = None;
        match (method, &uri) {
            ("textDocument/didOpen", Some(u)) => {
                if let Some(t) = params.pointer("/textDocument/text").and_then(|t| t.as_str()) {
                    self.docs.insert(u.clone(), text_stats(t));
                    change_bytes = Some(t.len() as u64);
                }
            }
            ("textDocument/didChange", Some(u)) => {
                let changes = params.get("contentChanges").and_then(|c| c.as_array());
                let mut total = 0u64;
                for c in changes.into_iter().flatten() {
                    let text = c.get("text").and_then(|t| t.as_str()).unwrap_or("");
                    total += text.len() as u64;
                    if c.get("range").is_none() {
                        self.docs.insert(u.clone(), text_stats(text));
                    }
                }
                change_bytes = Some(total);
            }
            _ => {}
        }
        let mut a = self.file_attrs(params);
        a.insert("method".into(), method.into());
        if let Some(v) = version {
            a.insert("doc.version".into(), v.into());
        }
        if let Some(b) = change_bytes {
            a.insert("change.bytes".into(), b.into());
        }
        self.sink.event_at("tap", "lsp.notify", self.now, a);
        if let Some(u) = &uri {
            match method {
                "textDocument/didOpen" | "textDocument/didChange" | "textDocument/didSave" => {
                    let m = method.trim_start_matches("textDocument/").to_string();
                    self.pending.insert(u.clone(), Trigger { method: m, start: ts_ns, version });
                }
                "textDocument/didClose" => {
                    self.docs.remove(u);
                    self.pending.remove(u);
                }
                _ => {}
            }
        }
    }

    fn server_notify(&mut self, method: &str, msg: &Value) {
        let params = msg.get("params").unwrap_or(&Value::Null);
        match method {
            "textDocument/publishDiagnostics" => self.diagnostics(params),
            "$/progress" => self.progress(params),
            "window/logMessage" | "window/showMessage" => {
                let t = params.get("type").and_then(|t| t.as_i64()).unwrap_or(0);
                if t == 1 || t == 2 {
                    let text = params.get("message").and_then(|m| m.as_str()).unwrap_or("");
                    let cut: String = text.chars().take(300).collect();
                    let lvl = if t == 1 { "error" } else { "warning" };
                    self.sink.event_at("tap", "lsp.log", self.now, attrs! {"level" => lvl, "msg" => cut});
                }
            }
            _ => {}
        }
    }

    fn diagnostics(&mut self, params: &Value) {
        let Some(uri) = params.get("uri").and_then(|u| u.as_str()) else { return };
        let mut counts = [0u64; 4];
        let diags = params.get("diagnostics").and_then(|d| d.as_array());
        for d in diags.into_iter().flatten() {
            let s = d.get("severity").and_then(|s| s.as_u64()).unwrap_or(1).clamp(1, 4);
            counts[s as usize - 1] += 1;
        }
        let trig = self.pending.remove(uri);
        let fake = serde_json::json!({"textDocument": {"uri": uri}});
        let mut a = self.file_attrs(&fake);
        let (name, start, version) = match &trig {
            Some(t) => (t.method.as_str(), t.start, t.version),
            None => ("background", self.now, None),
        };
        a.insert("trigger".into(), if trig.is_some() { name } else { "background" }.into());
        if let Some(v) = version.or_else(|| params.get("version").and_then(|v| v.as_i64())) {
            a.insert("doc.version".into(), v.into());
        }
        a.insert("diag.count".into(), counts.iter().sum::<u64>().into());
        a.insert("diag.error".into(), counts[0].into());
        a.insert("diag.warning".into(), counts[1].into());
        a.insert("diag.info".into(), counts[2].into());
        a.insert("diag.hint".into(), counts[3].into());
        self.sink.span_at("tap", "lsp.diagnostics", start, self.now, a);
    }

    fn progress(&mut self, params: &Value) {
        let Some(token) = params.get("token") else { return };
        let k = key(token);
        let val = params.get("value").unwrap_or(&Value::Null);
        match val.get("kind").and_then(|k| k.as_str()) {
            Some("begin") => {
                let title = val.get("title").and_then(|t| t.as_str()).map(|s| s.to_string());
                self.progress.insert(k, (self.now, title));
            }
            Some("end") => {
                if let Some((start, title)) = self.progress.remove(&k) {
                    self.sink.span_at("tap", "lsp.progress", start, self.now, attrs! {"token" => token.clone(), "title" => title});
                }
            }
            _ => {}
        }
    }
}
