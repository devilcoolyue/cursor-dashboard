//! Retain complete update recovery points; never follow links or remove foreign files.
use std::{
    fs, io,
    path::Path,
    time::{Duration, SystemTime},
};

fn identifier(name: &str) -> bool {
    name.len() == 64
        && name
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn size(path: &Path) -> io::Result<u64> {
    let mut bytes = 0u64;
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let info = fs::symlink_metadata(entry.path())?;
        if info.is_symlink() {
            return Err(io::Error::other("Backup contains a symbolic link"));
        }
        bytes = bytes.saturating_add(if info.is_dir() {
            size(&entry.path())?
        } else {
            info.len()
        });
    }
    Ok(bytes)
}

pub fn prune(directory: &Path, current: &str, keep: usize, budget: u64) -> io::Result<()> {
    if fs::symlink_metadata(directory)?.is_symlink() {
        return Err(io::Error::other("Backup directory is a symbolic link"));
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let info = fs::symlink_metadata(entry.path())?;
        if !info.is_dir() || info.is_symlink() {
            continue;
        }
        if let Some(id) = name.strip_prefix(".partial-") {
            if identifier(id)
                && SystemTime::now()
                    .duration_since(info.modified()?)
                    .unwrap_or_default()
                    > Duration::from_secs(86400)
            {
                // A crashed copy cannot be a recovery point. remove_dir_all does not follow links.
                fs::remove_dir_all(entry.path())?;
            }
        } else if identifier(&name) {
            entries.push((info.modified()?, name, entry.path(), size(&entry.path())?));
        }
    }
    entries.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
    let mut count = entries.len();
    let mut bytes: u64 = entries.iter().map(|item| item.3).sum();
    for (_, name, path, length) in entries {
        if count <= keep && bytes <= budget {
            break;
        }
        if name == current {
            continue;
        }
        fs::remove_dir_all(path)?;
        count -= 1;
        bytes = bytes.saturating_sub(length);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn budgets_preserve_current_recovery_point_and_unrelated_entries() {
        let root = std::env::temp_dir().join(format!(
            "cursor-backup-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let current = format!("{:064x}", 0);
        for i in 0..5 {
            let path = root.join(format!("{i:064x}"));
            fs::create_dir(&path).unwrap();
            fs::write(path.join("synthetic"), [0u8; 8]).unwrap();
        }
        fs::create_dir(root.join("foreign")).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(root.join("foreign"), root.join(format!("{:064x}", 99)))
            .unwrap();
        prune(&root, &current, 3, 1024).unwrap();
        assert!(root.join(&current).exists());
        assert_eq!(
            (0..5)
                .filter(|i| root.join(format!("{i:064x}")).exists())
                .count(),
            3
        );
        prune(&root, &current, 3, 1).unwrap();
        assert!(root.join(&current).exists());
        assert_eq!(
            (0..5)
                .filter(|i| root.join(format!("{i:064x}")).exists())
                .count(),
            1
        );
        assert!(root.join("foreign").exists());
        fs::remove_dir_all(root).unwrap();
    }
}
