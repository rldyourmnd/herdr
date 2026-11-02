"""Shared, dependency-free profile and environment contract for desktop hosts."""
import json
import os
from pathlib import Path
import re

TERMINAL_ENV = {
    "TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
    "TERMINFO", "TERMINFO_DIRS", "COLORFGBG", "NO_COLOR", "CLICOLOR",
    "CLICOLOR_FORCE", "VTE_VERSION", "KITTY_WINDOW_ID", "WEZTERM_PANE",
    "ITERM_SESSION_ID", "ALACRITTY_SOCKET", "ALACRITTY_WINDOW_ID",
    "GNOME_TERMINAL_SERVICE", "GNOME_TERMINAL_SCREEN", "TERMINAL_EMULATOR",
    "TMUX", "TMUX_PANE", "STY", "WINDOWID", "SSH_TTY",
}
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
UI_KEYS = {
    "panel_bg", "sidebar_bg", "active_row_bg", "selection_bg", "surface0",
    "surface1", "surface_dim", "overlay0", "overlay1", "text", "subtext0",
    "accent", "mauve", "green", "yellow", "red", "blue", "teal", "peach",
}


def validate_profile(data):
    if data.get("format") != 1:
        raise ValueError("unsupported desktop profile format")
    ui = data.get("ui_colors", {})
    terminal = data.get("terminal", {})
    if set(ui) != UI_KEYS:
        raise ValueError("desktop profile must specify every Herdr UI color")
    palette = terminal.get("palette", [])
    if not isinstance(palette, list) or len(palette) != 16:
        raise ValueError("desktop profile must specify 16 ANSI colors")
    fields = ["background", "foreground", "cursor",
              "selection_background", "selection_foreground"]
    colors = list(ui.values()) + palette + [terminal.get(k) for k in fields]
    if any(not isinstance(c, str) or not COLOR.fullmatch(c) for c in colors):
        raise ValueError("desktop colors must be opaque #RRGGBB values")
    if terminal["background"] != ui["panel_bg"] or terminal["foreground"] != ui["text"]:
        raise ValueError("terminal defaults must match the Herdr UI")
    return data


def load_profile(path):
    with Path(path).open(encoding="utf-8") as stream:
        return validate_profile(json.load(stream))


def child_environment(parent=None):
    source = os.environ if parent is None else parent
    result = {k: v for k, v in source.items()
              if not k.startswith("HERDR_") and k not in TERMINAL_ENV}
    result.update(TERM="xterm-256color", COLORTERM="truecolor",
                  TERM_PROGRAM="herdr-desktop", TERM_PROGRAM_VERSION="0.1.0")
    return result


def theme_config(original, profile):
    """Replace only theme tables; preserve all non-theme configuration."""
    output = []
    inside = False
    for line in original.splitlines(keepends=True):
        match = re.match(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$", line)
        if match:
            section = match.group(1).strip()
            inside = section == "theme" or section.startswith("theme.")
        if not inside:
            output.append(line)
    body = "".join(output).rstrip()
    theme = '\n\n[theme]\nname = "gruvbox-light"\nauto_switch = false\n\n[theme.custom]\n'
    return body + theme + "".join(
        f'{key} = "{value}"\n' for key, value in profile["ui_colors"].items())
