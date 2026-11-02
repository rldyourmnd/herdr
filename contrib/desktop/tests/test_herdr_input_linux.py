#!/usr/bin/python3
"""Full keyboard/mouse/lifecycle test in a disposable Herdr session.
Run under dbus-run-session -- xvfb-run. No LLMs or default-session operations.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
RAW_PROGRAM = r'''
import json,os,pathlib,select,sys,termios
path=pathlib.Path(sys.argv[1]); fd=0
a=termios.tcgetattr(fd)
a[0] &= ~(termios.BRKINT|termios.ICRNL|termios.INPCK|termios.ISTRIP|termios.IXON)
a[3] &= ~(termios.ECHO|termios.ICANON|termios.IEXTEN|termios.ISIG)
a[6][termios.VMIN]=1;a[6][termios.VTIME]=0
termios.tcsetattr(fd,termios.TCSANOW,a)
data=bytearray()
def save():
    out=path.with_suffix(".tmp")
    out.write_text(json.dumps({"pid":os.getpid(),"tty":os.ttyname(fd),
                              "data":list(data)}))
    out.replace(path)
save()
print("\033[2J\033[HNative Herdr keyboard/mouse acceptance",flush=True)
while True:
    if select.select([fd],[],[],.1)[0]:
        chunk=os.read(fd,4096)
        if not chunk:break
        data.extend(chunk);save()
'''


def eventually(action, check, seconds=12):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = action()
            if check(last):
                return last
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(.1)
    raise AssertionError(f"acceptance condition timed out: {last!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--herdr-binary", required=True, type=Path)
    options = parser.parse_args()
    binary = options.herdr_binary.resolve()
    session = "desktop-input-" + uuid.uuid4().hex[:12]
    assert session != "default"
    with tempfile.TemporaryDirectory(prefix="herdr-desktop-input-", dir="/var/tmp") as tmp:
        root = Path(tmp)
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
        env.update(XDG_CONFIG_HOME=str(root/"config"), XDG_STATE_HOME=str(root/"state"),
                   HERDR_CONFIG_PATH=str(root/"config.toml"), GDK_BACKEND="x11",
                   XDG_RUNTIME_DIR=str(root/"runtime"), GTK_USE_PORTAL="0",
                   GIO_USE_VFS="local", GSETTINGS_BACKEND="memory", NO_AT_BRIDGE="1")
        (root/"runtime").mkdir(mode=0o700)
        env.pop("WAYLAND_DISPLAY", None)
        (root/"config.toml").write_text(
            'onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n')
        (root/"raw_program.py").write_text(RAW_PROGRAM)
        wrapper = root/"herdr-client"
        binding = {k: env[k] for k in
                   ["XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "HERDR_CONFIG_PATH"]}
        wrapper.write_text("#!/bin/sh\n" +
                           "".join("export "+k+"="+shlex.quote(v)+"\n"
                                   for k, v in binding.items()) +
                           "exec "+shlex.join([str(binary), "--session", session])+"\n")
        wrapper.chmod(0o755)
        profile = json.loads((SOURCE/"themes/nddev-warm-light.json").read_text())
        profile["herdr_binary"] = str(wrapper)
        (root/"profile.json").write_text(json.dumps(profile))
        subprocess.run([str(binary), "config", "check"], env=env, check=True,
                       capture_output=True, text=True)
        gui = None
        ready = False
        bus = None
        log = (root/"gui.log").open("w")

        def cli(*args):
            p = subprocess.run([str(binary), "--session", session, *args],
                               env=env, check=True, capture_output=True,
                               text=True, timeout=12)
            return json.loads(p.stdout) if p.stdout.strip() else None

        def launch():
            return subprocess.Popen([
                "/usr/bin/python3", str(SOURCE/"linux/herdr_desktop.py"),
                "--profile", str(root/"profile.json"),
                "--application-id", "dev.herdr.Herdr.InputAcceptance",
            ], env=env, stdout=log, stderr=log)

        def input_bytes():
            return bytes(json.loads((root/"input.json").read_text())["data"])

        def inject(marker):
            windows = eventually(
                lambda: subprocess.check_output(
                    ["xdotool", "search", "--onlyvisible", "--class", "dev.herdr.Herdr"],
                    env=env, text=True).split(), lambda x: bool(x))
            assert len(windows) == 1, windows
            window = windows[0]
            subprocess.run(["xdotool", "windowfocus", "--sync", window], env=env, check=True)
            for n in range(24):
                subprocess.run(["xdotool", "mousemove", "--window", window,
                                str(700+n*3), str(220+n%5)], env=env, check=True)
            subprocess.run(["xdotool", "type", "--clearmodifiers", "--window", window,
                            "--delay", "15", "--", marker], env=env, check=True)

        try:
            # A private test bus without service activation cannot touch the user's
            # portals, keyring, accessibility services, or document FUSE mounts.
            (root/"dbus.conf").write_text(
                '<busconfig><type>session</type><auth>EXTERNAL</auth>'
                '<listen>unix:tmpdir=' + str(root) + '</listen>'
                '<policy context="default"><allow own="*"/>'
                '<allow send_destination="*"/><allow receive_sender="*"/>'
                '</policy></busconfig>')
            bus = subprocess.Popen(
                ["dbus-daemon", "--nofork", "--print-address=1",
                 "--config-file="+str(root/"dbus.conf")],
                env=env, stdout=subprocess.PIPE, stderr=log, text=True)
            address = bus.stdout.readline().strip()
            assert address.startswith("unix:"), "private test bus did not start"
            env["DBUS_SESSION_BUS_ADDRESS"] = address
            gui = launch()
            status = eventually(lambda: cli("status", "--json"),
                                lambda d: d["server"]["running"])
            socket = Path(status["server"]["socket"])
            assert socket.is_relative_to(root), socket
            ready = True
            panes = cli("pane", "list")["result"]["panes"]
            assert len(panes) == 1, panes
            pane = panes[0]["pane_id"]
            cli("pane", "run", pane, "/usr/bin/python3",
                str(root/"raw_program.py"), str(root/"input.json"))
            before = eventually(lambda: json.loads((root/"input.json").read_text()),
                                lambda x: bool(x.get("pid")))
            inject("nddev-input-ok")
            eventually(input_bytes, lambda b: b == b"nddev-input-ok")
            # Closing/reopening the GUI must leave the program and its raw tty intact.
            gui.terminate()
            gui.wait(timeout=8)
            gui = None
            time.sleep(.4)
            assert cli("status", "--json")["server"]["running"]
            assert json.loads((root/"input.json").read_text())["pid"] == before["pid"]
            gui = launch()
            eventually(lambda: cli("pane", "list"),
                       lambda x: x["result"]["panes"][0]["pane_id"] == pane)
            inject("-reattached")
            eventually(input_bytes, lambda b: b == b"nddev-input-ok-reattached")
            after = json.loads((root/"input.json").read_text())
            assert after["pid"] == before["pid"] and after["tty"] == before["tty"]
            fd = os.open(before["tty"], os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                import termios
                attrs = termios.tcgetattr(fd)
                assert not attrs[3] & (termios.ECHO | termios.ICANON)
            finally:
                os.close(fd)
            print(json.dumps({"keyboard_without_enter": True, "mouse_junk": False,
                              "reattach_preserved_process": True,
                              "inner_tty_raw_preserved": True, "session": session}))
        except BaseException:
            log.flush()
            print((root/"gui.log").read_text()[-5000:], file=sys.stderr)
            raise
        finally:
            if gui and gui.poll() is None:
                gui.terminate()
                gui.wait(timeout=8)
            if ready:
                # The socket and unique session were proven to belong to this test above.
                subprocess.run([str(binary), "session", "stop", session, "--json"],
                               env=env, check=True, capture_output=True, timeout=20)
                subprocess.run([str(binary), "session", "delete", session, "--json"],
                               env=env, check=True, capture_output=True, timeout=20)
            if bus and bus.poll() is None:
                bus.terminate()
                bus.wait(timeout=5)
            log.close()


if __name__ == "__main__":
    main()
