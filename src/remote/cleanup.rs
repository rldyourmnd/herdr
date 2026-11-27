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

/// Runs [`run`] and exits on SIGINT, SIGTERM or SIGHUP. A process that already
/// owns a termination handler keeps it; its next start prunes what it leaves.
pub(crate) fn exit_on_termination() {
    let _ = ctrlc::try_set_handler(|| {
        run();
        std::process::exit(130);
    });
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

    #[test]
    fn run_removes_registered_folders() {
        let dir = unique_temp_path("registered");
        std::fs::create_dir(&dir).unwrap();
        DIRECTORIES.lock().unwrap().push(dir.clone());
        run();
        assert!(!dir.exists());
    }
}
