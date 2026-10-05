//! Forwards SIGTERM/SIGINT/SIGHUP received by the tap to the child (self-pipe + forwarder thread).

use std::sync::atomic::{AtomicI32, Ordering};

static WRITE_FD: AtomicI32 = AtomicI32::new(-1);
const SIGS: [libc::c_int; 3] = [libc::SIGTERM, libc::SIGINT, libc::SIGHUP];

extern "C" fn on_signal(sig: libc::c_int) {
    let fd = WRITE_FD.load(Ordering::Relaxed);
    let b = sig as u8;
    // write is async-signal-safe; a full pipe just drops the repeat.
    unsafe { libc::write(fd, &b as *const u8 as *const libc::c_void, 1) };
}

/// Installs handlers (call before spawning the child); returns the pipe read fd, or -1 on failure.
pub fn install() -> i32 {
    let mut fds = [0i32; 2];
    unsafe {
        if libc::pipe(fds.as_mut_ptr()) != 0 {
            return -1;
        }
        for fd in fds {
            libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
        }
        libc::fcntl(fds[1], libc::F_SETFL, libc::O_NONBLOCK);
        WRITE_FD.store(fds[1], Ordering::Relaxed);
        for s in SIGS {
            let mut sa: libc::sigaction = std::mem::zeroed();
            sa.sa_sigaction = on_signal as *const () as usize;
            sa.sa_flags = libc::SA_RESTART;
            libc::sigemptyset(&mut sa.sa_mask);
            libc::sigaction(s, &sa, std::ptr::null_mut());
        }
    }
    fds[0]
}

/// Thread that relays each received signal to `pid`.
pub fn forward_to(read_fd: i32, pid: i32) {
    if read_fd < 0 {
        return;
    }
    std::thread::spawn(move || loop {
        let mut b = 0u8;
        let n = unsafe { libc::read(read_fd, &mut b as *mut u8 as *mut libc::c_void, 1) };
        if n == 1 {
            unsafe { libc::kill(pid, b as libc::c_int) };
        } else if n == 0 || std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted {
            return;
        }
    });
}
