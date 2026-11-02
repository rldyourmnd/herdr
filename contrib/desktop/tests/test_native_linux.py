#!/usr/bin/python3
"""Run under dbus-run-session + xvfb-run in CI; uses no Herdr server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="herdr-desktop-") as tmp:
        root = Path(tmp)
        profile = json.loads((SOURCE / "themes/nddev-warm-light.json").read_text())
        profile["herdr_binary"] = str(SOURCE / "tests/osc_probe.py")
        config = root / "profile.json"
        config.write_text(json.dumps(profile))
        env = dict(os.environ, DESKTOP_PROBE_OUTPUT=str(root / "osc.json"),
                   TERM_PROGRAM="foreign-terminal", NO_COLOR="1")
        result = subprocess.run([
            sys.executable, str(SOURCE / "linux/herdr_desktop.py"),
            "--profile", str(config), "--snapshot", str(root / "window.png"),
            "--application-id", "dev.herdr.Herdr.NativeTest",
        ], env=env, capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise RuntimeError(result.stderr)
        report = json.loads((root / "osc.json").read_text())
        expected = {"osc:10": profile["terminal"]["foreground"],
                    "osc:11": profile["terminal"]["background"]}
        expected.update({"ansi:" + str(i): value
                         for i, value in enumerate(profile["terminal"]["palette"])})
        assert report["colors"] == expected, report
        assert report["env"]["TERM_PROGRAM"] == "herdr-desktop", report
        assert report["env"]["HERDR_ENV"] is None, report
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        image = GdkPixbuf.Pixbuf.new_from_file(str(root / "window.png"))
        assert image.get_width() > 1000 and image.get_height() > 600
        pixels, channels = image.get_pixels(), image.get_n_channels()
        rgb = bytes.fromhex(profile["terminal"]["background"].lstrip("#"))
        matches = sum(pixels[i:i + 3] == rgb for i in range(0, len(pixels), channels))
        assert matches > 100000, "window did not paint its own opaque background"
        print("Native GTK/VTE: 18 OSC colors, sanitized environment and opaque image verified")


if __name__ == "__main__":
    main()
