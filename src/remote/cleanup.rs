//! Process-wide cleanup of the SSH resources a Herdr process creates.
//!
//! A managed SSH config folder is removed by its `Drop`, which a termination
//! signal or `SIGKILL` bypasses. Each process therefore registers its folders
//! and long-lived `ssh` children here: a termination signal removes them, and
//! the first managed folder a process creates prunes folders left by dead
//! processes of the same user.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

static DIRECTORIES: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());
static CHILDREN: Mutex<Vec<u32>> = Mutex::new(Vec::new());
#[cfg(unix)]
static PRUNE: std::sync::Once = std::sync::Once::new();

pub(super) fn register_directory(path: &Path) {
    #[cfg(unix)]
    PRUNE.call_once(|| {
        let mut bases = vec![std::env::temp_dir()];
        let short_tmp = PathBuf::from("/tmp");
        if bases.first() != Some(&short_tmp) {
            bases.push(short_tmp);
        }
        for base in bases {
            prune_stale_directories(&base);
        }
    });
    if let Ok(mut directories) = DIRECTORIES.lock() {
        directories.push(path.to_owned());
    }
}

pub(super) fn unregister_directory(path: &Path) {
    if let Ok(mut directories) = DIRECTORIES.lock() {
        directories.retain(|known| known != path);
    }
}

/// Keeps a spawned `ssh` child on the stop list until the guard drops.
pub(super) struct ChildGuard(u32);

impl ChildGuard {
    pub(super) fn new(pid: u32) -> Self {
        if let Ok(mut children) = CHILDREN.lock() {
            children.push(pid);
        }
        Self(pid)
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if let Ok(mut children) = CHILDREN.lock() {
            children.retain(|known| *known != self.0);
        }
    }
}

/// Stops the registered `ssh` children and removes the registered folders.
pub(crate) fn run() {
    let children = CHILDREN
        .lock()
        .map(|mut children| std::mem::take(&mut *children))
        .unwrap_or_default();
    crate::platform::signal_processes(&children, crate::platform::Signal::Terminate);
    let directories = DIRECTORIES
        .lock()
        .map(|mut directories| std::mem::take(&mut *directories))
        .unwrap_or_default();
    for directory in directories {
        let _ = std::fs::remove_dir_all(directory);
    }
}

/// Runs [`run`] on SIGTERM, SIGHUP or SIGINT, then ends the process by the same
/// signal. Only signals at their default action are taken: an ignored signal stays
/// ignored and an existing handler stays in place; the next start prunes what such
/// a process leaves.
#[cfg(unix)]
pub(crate) fn exit_on_termination() {
    use std::io::Read as _;
    use std::os::fd::FromRawFd as _;
    use std::sync::Once;

    static INSTALL: Once = Once::new();
    INSTALL.call_once(|| {
        let mut fds = [0; 2];
        if unsafe { libc::pipe(fds.as_mut_ptr()) } != 0 {
            return;
        }
        unsafe {
            libc::fcntl(fds[0], libc::F_SETFD, libc::FD_CLOEXEC);
            libc::fcntl(fds[1], libc::F_SETFD, libc::FD_CLOEXEC);
        }
        SIGNAL_PIPE.store(fds[1], std::sync::atomic::Ordering::Release);
        let mut taken = false;
        for signal in [libc::SIGTERM, libc::SIGHUP, libc::SIGINT] {
            taken |= take_default_signal(signal);
        }
        if !taken {
            return;
        }
        let mut reader = unsafe { std::fs::File::from_raw_fd(fds[0]) };
        let _ = std::thread::Builder::new()
            .name("ssh-cleanup".into())
            .spawn(move || {
                let mut signal = [0u8];
                if reader.read_exact(&mut signal).is_ok() {
                    run();
                    unsafe {
                        libc::signal(libc::c_int::from(signal[0]), libc::SIG_DFL);
                        libc::raise(libc::c_int::from(signal[0]));
                    }
                    std::process::exit(128 + i32::from(signal[0]));
                }
            });
    });
}

#[cfg(not(unix))]
pub(crate) fn exit_on_termination() {}

#[cfg(unix)]
static SIGNAL_PIPE: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);

#[cfg(unix)]
extern "C" fn forward_signal(signal: libc::c_int) {
    let fd = SIGNAL_PIPE.load(std::sync::atomic::Ordering::Acquire);
    let byte = signal as u8;
    // SAFETY: write is async-signal-safe; a failed write leaves the default exit to a second signal.
    unsafe {
        libc::write(fd, (&raw const byte).cast(), 1);
    }
}

/// Installs [`forward_signal`] for `signal` only when its action is the default.
#[cfg(unix)]
fn take_default_signal(signal: libc::c_int) -> bool {
    unsafe {
        let mut current: libc::sigaction = std::mem::zeroed();
        if libc::sigaction(signal, std::ptr::null(), &mut current) != 0
            || current.sa_sigaction != libc::SIG_DFL
        {
            return false;
        }
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = forward_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
        action.sa_flags = libc::SA_RESTART;
        libc::sigemptyset(&mut action.sa_mask);
        libc::sigaction(signal, &action, std::ptr::null_mut()) == 0
    }
}

/// Removes `herdr-ssh-<pid>-<n>` folders in `base` that belong to this user and
/// whose process no longer runs. Symlinks and other users' folders stay.
#[cfg(unix)]
fn prune_stale_directories(base: &Path) {
    use std::os::unix::fs::MetadataExt as _;

    let uid = unsafe { libc::geteuid() };
    let Ok(entries) = std::fs::read_dir(base) else {
        return;
    };
    let own = std::process::id();
    for entry in entries.flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(stale_candidate_pid) else {
            continue;
        };
        if pid == own || crate::platform::process_exists(pid) {
            continue;
        }
        let path = entry.path();
        let owned = std::fs::symlink_metadata(&path)
            .is_ok_and(|metadata| metadata.is_dir() && metadata.uid() == uid);
        if owned {
            let _ = std::fs::remove_dir_all(path);
        }
    }
}

/// The pid in a managed folder name `herdr-ssh-<pid>-<attempt>`. Other names,
/// such as `herdr-ssh-agent-<pid>`, return `None`.
#[cfg_attr(not(unix), allow(dead_code))]
fn stale_candidate_pid(name: &str) -> Option<u32> {
    let (pid, attempt) = name.strip_prefix("herdr-ssh-")?.split_once('-')?;
    if pid.is_empty()
        || attempt.is_empty()
        || !pid.bytes().all(|byte| byte.is_ascii_digit())
        || !attempt.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    pid.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_managed_folder_names_are_candidates() {
        assert_eq!(stale_candidate_pid("herdr-ssh-4242-0"), Some(4242));
        assert_eq!(stale_candidate_pid("herdr-ssh-4242-17"), Some(4242));
        assert_eq!(stale_candidate_pid("herdr-ssh-agent-4242"), None);
        assert_eq!(stale_candidate_pid("herdr-ssh-4242"), None);
        assert_eq!(stale_candidate_pid("herdr-ssh--0"), None);
        assert_eq!(stale_candidate_pid("herdr-ssh-12a-0"), None);
        assert_eq!(stale_candidate_pid("hssh-1000"), None);
    }

    fn unique_temp_path(name: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        std::env::temp_dir().join(format!("herdr-{name}-{}-{nanos}", std::process::id()))
    }

    #[cfg(unix)]
    #[test]
    fn prune_removes_only_folders_of_dead_processes() {
        let base = unique_temp_path("prune");
        std::fs::create_dir(&base).unwrap();
        let mut dead = std::process::Command::new("true").spawn().unwrap();
        let dead_pid = dead.id();
        dead.wait().unwrap();
        let stale = base.join(format!("herdr-ssh-{dead_pid}-0"));
        let live = base.join(format!("herdr-ssh-{}-0", std::process::id()));
        let agent = base.join(format!("herdr-ssh-agent-{dead_pid}"));
        let target = base.join("target");
        let link = base.join(format!("herdr-ssh-{dead_pid}-1"));
        for dir in [&stale, &live, &agent, &target] {
            std::fs::create_dir(dir).unwrap();
        }
        std::fs::write(stale.join("config"), "Host *\n").unwrap();
        std::os::unix::fs::symlink(&target, &link).unwrap();

        prune_stale_directories(&base);

        assert!(!stale.exists());
        assert!(live.exists());
        assert!(agent.exists());
        assert!(link.symlink_metadata().is_ok());
        assert!(target.exists());
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn only_signals_at_their_default_action_are_taken() {
        unsafe {
            libc::signal(libc::SIGUSR1, libc::SIG_IGN);
            assert!(!take_default_signal(libc::SIGUSR1));
            let mut current: libc::sigaction = std::mem::zeroed();
            libc::sigaction(libc::SIGUSR1, std::ptr::null(), &mut current);
            assert_eq!(current.sa_sigaction, libc::SIG_IGN);

            libc::signal(libc::SIGUSR1, libc::SIG_DFL);
            assert!(take_default_signal(libc::SIGUSR1));
            libc::sigaction(libc::SIGUSR1, std::ptr::null(), &mut current);
            assert_ne!(current.sa_sigaction, libc::SIG_DFL);
            libc::signal(libc::SIGUSR1, libc::SIG_DFL);
        }
    }

    #[test]
    fn run_removes_registered_folders() {
        let dir = unique_temp_path("registered");
        std::fs::create_dir(&dir).unwrap();
        DIRECTORIES.lock().unwrap().push(dir.clone());
        run();
        assert!(!dir.exists());
    }
}
