use lsp_metrics::{sampler, Sink};

#[test]
fn sleep_sampled_then_exit() {
    let dir = std::env::temp_dir().join(format!("lsp-sampler-test-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::env::set_var("LSP_METRICS_DIR", &dir);
    let mut child = std::process::Command::new("sleep").arg("2").spawn().unwrap();
    let pid = child.id() as i32;
    // reap the child so it is not a zombie
    let waiter = std::thread::spawn(move || child.wait());
    let (sink, guard) = Sink::open(None);
    sampler::spawn(pid, sink.clone()).join().unwrap();
    guard.finish(sink);
    let _ = waiter.join();
    let mut text = String::new();
    for e in std::fs::read_dir(&dir).unwrap() {
        text += &std::fs::read_to_string(e.unwrap().path()).unwrap();
    }
    let rows: Vec<serde_json::Value> = text.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
    let samples: Vec<_> = rows.iter().filter(|r| r["name"] == "proc.sample").collect();
    let exits = rows.iter().filter(|r| r["name"] == "proc.exit").count();
    assert!(!samples.is_empty(), "no samples: {text}");
    let a = &samples[0]["attrs"];
    assert!(a["footprint"].as_u64().unwrap() > 0 && a["threads"].as_u64().unwrap() >= 1, "{a}");
    assert_eq!(exits, 1);
}
