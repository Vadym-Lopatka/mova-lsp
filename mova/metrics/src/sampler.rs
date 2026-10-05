//! Process sampler: polls a pid (and its child tree) from outside via libproc.
use crate::{attrs, Sink};
use std::mem::MaybeUninit;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

const TICK: Duration = Duration::from_millis(500);
const HEARTBEAT: Duration = Duration::from_secs(10);
const FOOTPRINT_STEP: u64 = 1 << 20;
const CPU_STEP_NS: u64 = 5_000_000;

#[derive(Default, Clone, Copy)]
struct Totals {
    footprint: u64,
    peak: u64,
    rss: u64,
    user_ns: u64,
    sys_ns: u64,
    threads: u64,
    procs: u64,
}

#[repr(C)]
struct TimebaseInfo {
    numer: u32,
    denom: u32,
}

extern "C" {
    fn mach_timebase_info(info: *mut TimebaseInfo) -> i32;
}

/// Mach absolute time -> ns factor (numer, denom).
pub fn timebase() -> (u64, u64) {
    let mut tb = TimebaseInfo { numer: 0, denom: 0 };
    unsafe { mach_timebase_info(&mut tb) };
    if tb.denom == 0 { (1, 1) } else { (tb.numer as u64, tb.denom as u64) }
}

pub fn mach_to_ns(t: u64, tb: (u64, u64)) -> u64 {
    (t as u128 * tb.0 as u128 / tb.1 as u128) as u64
}

/// CPU (user, sys) ns of one pid, or None if unreadable.
pub fn cpu_ns(pid: i32) -> Option<(u64, u64)> {
    let tb = timebase();
    let ri = rusage(pid)?;
    Some((mach_to_ns(ri.ri_user_time, tb), mach_to_ns(ri.ri_system_time, tb)))
}

fn rusage(pid: i32) -> Option<libc::rusage_info_v4> {
    let mut ri = MaybeUninit::<libc::rusage_info_v4>::zeroed();
    let rc = unsafe { libc::proc_pid_rusage(pid, libc::RUSAGE_INFO_V4, ri.as_mut_ptr() as *mut _) };
    if rc == 0 { Some(unsafe { ri.assume_init() }) } else { None }
}

fn threads(pid: i32) -> u64 {
    let mut ti = MaybeUninit::<libc::proc_taskinfo>::zeroed();
    let sz = std::mem::size_of::<libc::proc_taskinfo>() as i32;
    let n = unsafe { libc::proc_pidinfo(pid, libc::PROC_PIDTASKINFO, 0, ti.as_mut_ptr() as *mut _, sz) };
    if n == sz { unsafe { ti.assume_init() }.pti_threadnum.max(0) as u64 } else { 0 }
}

fn children(pid: i32, buf: &mut Vec<i32>) -> usize {
    buf.resize(256, 0);
    let n = unsafe { libc::proc_listchildpids(pid, buf.as_mut_ptr() as *mut _, (buf.len() * 4) as i32) };
    if n <= 0 { 0 } else { (n as usize / 4).min(buf.len()) }
}

/// Reads the whole tree; None when the root is gone.
fn read_tree(root: i32, tb: (u64, u64), stack: &mut Vec<i32>, buf: &mut Vec<i32>) -> Option<Totals> {
    let mut t = Totals::default();
    let rr = rusage(root)?;
    stack.clear();
    stack.push(root);
    let mut first = true;
    while let Some(pid) = stack.pop() {
        let ri = if first { Some(rr) } else { rusage(pid) };
        first = false;
        if let Some(ri) = ri {
            t.footprint += ri.ri_phys_footprint;
            t.peak += ri.ri_lifetime_max_phys_footprint;
            t.rss += ri.ri_resident_size;
            t.user_ns += mach_to_ns(ri.ri_user_time, tb);
            t.sys_ns += mach_to_ns(ri.ri_system_time, tb);
            t.threads += threads(pid);
            t.procs += 1;
        }
        let n = children(pid, buf);
        stack.extend(buf[..n].iter().copied().filter(|&c| c > 0));
    }
    Some(t)
}

/// Samples `pid` until it exits, emitting `proc.sample` and a final `proc.exit` (SCHEMA.md).
/// Never blocks the caller; returns the sampling thread.
pub fn spawn(pid: i32, sink: Sink) -> JoinHandle<()> {
    std::thread::Builder::new()
        .name("metrics-sampler".into())
        .spawn(move || run(pid, sink))
        .expect("spawn sampler")
}

fn run(pid: i32, sink: Sink) {
    let tb = timebase();
    let (mut stack, mut buf) = (Vec::new(), Vec::new());
    let mut last_emit: Option<(Totals, Instant)> = None;
    let mut max_peak = 0u64;
    let mut last = Totals::default();
    loop {
        let Some(t) = read_tree(pid, tb, &mut stack, &mut buf) else {
            sink.event("sampler", "proc.exit", attrs! {
                "pid" => pid, "footprint_peak" => max_peak,
                "cpu_user_ns" => last.user_ns, "cpu_sys_ns" => last.sys_ns,
            });
            return;
        };
        max_peak = max_peak.max(t.peak).max(t.footprint);
        last = t;
        let due = match &last_emit {
            None => true,
            Some((p, at)) => {
                t.footprint.abs_diff(p.footprint) >= FOOTPRINT_STEP
                    || (t.user_ns + t.sys_ns).abs_diff(p.user_ns + p.sys_ns) >= CPU_STEP_NS
                    || t.threads != p.threads
                    || t.procs != p.procs
                    || at.elapsed() >= HEARTBEAT
            }
        };
        if due {
            sink.sample("sampler", "proc.sample", attrs! {
                "pid" => pid, "footprint" => t.footprint, "footprint_peak" => max_peak,
                "rss" => t.rss, "cpu_user_ns" => t.user_ns, "cpu_sys_ns" => t.sys_ns,
                "threads" => t.threads, "procs" => t.procs,
            });
            last_emit = Some((t, Instant::now()));
        }
        std::thread::sleep(TICK);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mach_time_matches_wall() {
        let me = std::process::id() as i32;
        let (u0, s0) = cpu_ns(me).unwrap();
        let t0 = Instant::now();
        let mut x = 0u64;
        while t0.elapsed() < Duration::from_millis(200) {
            x = std::hint::black_box(x.wrapping_add(1));
        }
        let wall = t0.elapsed().as_nanos() as f64;
        let (u1, s1) = cpu_ns(me).unwrap();
        let cpu = ((u1 + s1) - (u0 + s0)) as f64;
        assert!((cpu / wall - 1.0).abs() < 0.2, "cpu {cpu} wall {wall}");
    }
}
