#!/usr/bin/env python3
"""Disposable child for native terminal acceptance; never touches Herdr sessions."""
import json
import os
from pathlib import Path
import re
import select
import sys
import termios
import time
import tty

fd = sys.stdin.fileno()
original = termios.tcgetattr(fd)
responses = bytearray()
try:
    tty.setraw(fd)
    query = "\033]10;?\033\\\033]11;?\033\\"
    query += "".join(f"\033]4;{i};?\033\\" for i in range(16))
    os.write(sys.stdout.fileno(), query.encode())
    deadline = time.monotonic() + 3
    pattern = re.compile(rb"\x1b\](10|11|4);(?:(\d+);)?rgb:([a-fA-F0-9]+)/([a-fA-F0-9]+)/([a-fA-F0-9]+)(?:\x07|\x1b\\)")
    while time.monotonic() < deadline:
        if select.select([fd], [], [], .1)[0]:
            responses.extend(os.read(fd, 8192))
        if len(pattern.findall(responses)) >= 18:
            break
finally:
    termios.tcsetattr(fd, termios.TCSANOW, original)

colors = {}
for command, index, r, g, b in pattern.findall(responses):
    rgb = [round(int(c, 16) * 255 / (16 ** len(c) - 1)) for c in (r, g, b)]
    key = "ansi:" + index.decode() if command == b"4" else "osc:" + command.decode()
    colors[key] = "#" + "".join(f"{c:02x}" for c in rgb)
report = {"colors": colors, "responses": responses.decode(errors="replace"),
          "env": {k: os.environ.get(k) for k in
                  ["TERM", "COLORTERM", "TERM_PROGRAM", "HERDR_ENV", "HERDR_SOCKET_PATH"]}}
Path(os.environ["DESKTOP_PROBE_OUTPUT"]).write_text(json.dumps(report, indent=2))
print("\033[2J\033[HHerdr Desktop — NDDev Warm Light\r\n\r\n")
print("Native terminal: foreground, background, ANSI palette, selection\r\n")
for i in range(16):
    print(f"\033[38;5;{i}m  ANSI {i:02d}  \033[0m", end="")
    if i in (7, 15):
        print("\r\n")
print("\r\n  Sample text: English · Русский · العربية · 中文\r\n")
print("  Selected text uses the warm highlight from the desktop profile.\r\n", flush=True)
time.sleep(8)
