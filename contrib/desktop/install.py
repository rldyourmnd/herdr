#!/usr/bin/env python3
"""Install the custom desktop host without replacing or stopping Herdr servers."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from desktop_profile import load_profile, theme_config

SOURCE = Path(__file__).resolve().parent


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(binary, profile_path, macos_executable=None):
    binary = binary.expanduser().absolute()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Herdr binary must be an executable file")
    profile = load_profile(profile_path)
    home = Path.home()
    config_dir = home / ".config/herdr"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / "config.toml"
    original = config.read_text() if config.exists() else "onboarding = false\n"
    candidate = theme_config(original, profile)
    with tempfile.NamedTemporaryFile(dir=config_dir, suffix=".toml") as temp:
        temp.write(candidate.encode())
        temp.flush()
        env = os.environ.copy()
        env["HERDR_CONFIG_PATH"] = temp.name
        subprocess.run([str(binary), "config", "check"], env=env, check=True)
    if original != candidate:
        # Caller keeps a dated full backup; this extra copy is a one-step rollback.
        if config.exists():
            shutil.copy2(config, config_dir / "config.before-desktop.toml")
        atomic_write(config, candidate.encode())
    runtime_profile = dict(profile, herdr_binary=str(binary))
    atomic_write(config_dir / "desktop.json",
                 (json.dumps(runtime_profile, indent=2) + "\n").encode())

    if platform.system() == "Linux":
        destination = home / ".local/share/herdr-desktop/0.1.0"
        for rel in ["desktop_profile.py", "linux/herdr_desktop.py"]:
            atomic_write(destination / rel, (SOURCE / rel).read_bytes(), 0o644)
        launcher = home / ".local/bin/herdr-desktop"
        # Retain the existing device's memory-guard startup, if present.
        prelude = ""
        if launcher.exists():
            for line in launcher.read_text().splitlines():
                if line.startswith("systemctl --user start ") and "herdr-" in line:
                    prelude += line + "\n"
        path = str(destination / "linux/herdr_desktop.py")
        # shlex.quote is shell quoting, not JSON or desktop-entry quoting.
        import shlex
        atomic_write(launcher, ("#!/bin/sh\n" + prelude +
                     "exec /usr/bin/python3 " + shlex.quote(path) + ' "$@"\n').encode(), 0o755)
        entry = home / ".local/share/applications/dev.herdr.Herdr.desktop"
        old = entry.read_text() if entry.exists() else (
            "[Desktop Entry]\nType=Application\nName=Herdr\nIcon=herdr\n"
            "Terminal=false\nCategories=Development;\nStartupWMClass=dev.herdr.Herdr\n")
        lines = [line for line in old.splitlines()
                 if not line.startswith(("Exec=", "TryExec="))]
        quoted = str(launcher).replace("\\", "\\\\").replace('"', '\\"')
        atomic_write(entry, ("\n".join(lines) +
                     '\nExec="' + quoted + '"\nTryExec=/usr/bin/python3\n').encode(), 0o644)
    elif platform.system() == "Darwin":
        if macos_executable is None:
            raise ValueError("macOS installation requires a separately built native executable")
        app = Path("/Applications/Herdr.app")
        target = app / "Contents/MacOS/Herdr"
        if not target.is_file():
            raise ValueError("install the existing Herdr.app bundle and icon first")
        atomic_write(target, macos_executable.read_bytes(), 0o755)
        # Keep embedded source aligned with the delivered executable.
        resources = app / "Contents/Resources/app-source"
        for rel in ["Package.swift", "Sources/HerdrDesktop/main.swift", "rebuild.sh"]:
            atomic_write(resources / rel, (SOURCE / "macos" / rel).read_bytes(), 0o644)
        (resources / "rebuild.sh").chmod(0o755)
        old_source = resources / "Sources/Herdr/main.swift"
        if old_source.exists():
            old_source.unlink()
        resolved = SOURCE / "macos/Package.resolved"
        if resolved.exists():
            atomic_write(resources / "Package.resolved", resolved.read_bytes(), 0o644)
        atomic_write(app / "Contents/Resources/ThirdPartyNotices/SwiftTerm-LICENSE",
                     (SOURCE / "macos/SwiftTerm-LICENSE").read_bytes(), 0o644)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    else:
        raise ValueError("this desktop host supports Linux and macOS")
    print(json.dumps({"host": platform.node(), "desktop_version": "0.1.0",
                      "profile": str(config_dir / "desktop.json"),
                      "herdr_binary": str(binary)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--herdr-binary", required=True, type=Path)
    parser.add_argument("--profile", type=Path,
                        default=SOURCE / "themes/nddev-warm-light.json")
    parser.add_argument("--macos-executable", type=Path)
    args = parser.parse_args()
    install(args.herdr_binary, args.profile, args.macos_executable)
