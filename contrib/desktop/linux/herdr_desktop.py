#!/usr/bin/python3
"""Standalone GTK/VTE host. Does not launch or configure another terminal app."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop_profile import child_environment, load_profile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte


def rgba(value):
    color = Gdk.RGBA()
    if not color.parse(value):
        raise ValueError("invalid desktop color")
    return color


def configure_terminal(terminal, profile):
    colors = profile["terminal"]
    terminal.set_colors(rgba(colors["foreground"]), rgba(colors["background"]),
                        [rgba(c) for c in colors["palette"]])
    terminal.set_color_cursor(rgba(colors["cursor"]))
    terminal.set_color_cursor_foreground(rgba(colors["background"]))
    terminal.set_color_highlight(rgba(colors["selection_background"]))
    terminal.set_color_highlight_foreground(rgba(colors["selection_foreground"]))
    terminal.set_font(Pango.FontDescription("Monospace 12"))
    terminal.set_scrollback_lines(10000)
    terminal.set_allow_hyperlink(True)


class Desktop(Gtk.Application):
    def __init__(self, options, profile):
        super().__init__(application_id=options.application_id,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.options, self.profile = options, profile
        self.window = None
        self.terminal = None

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", False)
        colors = self.profile["terminal"]
        css = Gtk.CssProvider()
        css.load_from_data((
            ".herdr-window, .herdr-window headerbar {"
            f'background: {colors["background"]}; color: {colors["foreground"]};'
            "background-image: none; box-shadow: none; }"
        ).encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self, title="Herdr")
        self.window.set_wmclass("dev.herdr.Herdr", "dev.herdr.Herdr")
        self.window.set_icon_name("herdr")
        self.window.get_style_context().add_class("herdr-window")
        self.window.set_default_size(1420, 900)
        header = Gtk.HeaderBar(title="Herdr", show_close_button=True)
        self.window.set_titlebar(header)
        self.terminal = Vte.Terminal()
        configure_terminal(self.terminal, self.profile)
        self.terminal.connect("child-exited", self.child_exited)
        self.terminal.connect("key-press-event", self.key_press)
        self.window.add(self.terminal)
        self.window.show_all()
        env = child_environment()
        env["VTE_VERSION"] = str(Vte.get_major_version()*10000 +
                                 Vte.get_minor_version()*100 + Vte.get_micro_version())
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, str(Path.home()),
            [self.profile["herdr_binary"]], [f"{k}={v}" for k, v in env.items()],
            GLib.SpawnFlags.DEFAULT, None, None, -1, None, self.spawned, None)

    def spawned(self, terminal, pid, error, user_data):
        if error:
            print(f"Herdr desktop could not start its client: {error}", file=sys.stderr)
            self.quit()
        elif self.options.snapshot:
            GLib.timeout_add(1800, self.snapshot)

    def child_exited(self, terminal, status):
        if self.window:
            self.window.destroy()
            self.window = None

    def key_press(self, terminal, event):
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if modifiers == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            if event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
                terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
                terminal.paste_clipboard()
                return True
        return False

    def snapshot(self):
        # Render only our widget tree: no desktop capture or Screen Recording permission.
        import cairo
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                     self.window.get_allocated_width(),
                                     self.window.get_allocated_height())
        self.window.draw(cairo.Context(surface))
        surface.write_to_png(self.options.snapshot)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path,
                        default=Path.home()/".config/herdr/desktop.json")
    parser.add_argument("--application-id", default="dev.herdr.Herdr")
    parser.add_argument("--snapshot")
    options = parser.parse_args()
    profile = load_profile(options.profile)
    binary = Path(profile["herdr_binary"])
    if not binary.is_absolute() or not binary.is_file():
        raise ValueError("desktop profile needs an existing absolute Herdr binary path")
    return Desktop(options, profile).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
