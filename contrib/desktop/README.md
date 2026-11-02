# Standalone desktop hosts (custom fork)

These desktop hosts attach the existing Herdr TUI to a terminal widget owned by
the Herdr application. Linux uses GTK 3 / VTE 2.91; macOS uses AppKit / SwiftTerm.
They launch the selected Herdr binary directly, with their own application
identity and no external terminal application or terminal configuration.

The included NDDev Warm Light profile defines all 19 Herdr UI colors, terminal
foreground/background, 16 ANSI entries, cursor and selection. Every color is
opaque. Its light appearance is fixed independently of OS appearance. Installing
the profile preserves non-theme Herdr configuration and saved SSH machines.

The desktop host is version 0.1.0; the Herdr runtime stays on its installed
version. This is the custom fork's packaging, not the new herdr-core/hc product.
Refreshing the UI must not stop, replace or migrate the Herdr server.

## Linux

Requires the distribution's Python 3, PyGObject, GTK 3, VTE 2.91 and pycairo.
For Ubuntu these are provided by python3-gi, gir1.2-gtk-3.0,
gir1.2-vte-2.91 and python3-gi-cairo.

Run the installer with the absolute path to the existing Herdr binary:

    python3 contrib/desktop/install.py --herdr-binary /absolute/path/to/herdr

Close the previous desktop UI only, then start the Herdr app from the launcher
or run herdr-desktop. The command-line Herdr binary and the background server
retain their existing installation and update owner.

## macOS

Requires Swift 6.2 or newer. SwiftTerm is pinned to an immutable source revision.
Use the existing native SwiftPM cache and scratch path:

    swift build --package-path contrib/desktop/macos --scratch-path <native-cache>/herdr-desktop/build --cache-path <native-cache> -c release

Close only the old Herdr.app UI. Keep its background server running. Install the
built HerdrDesktop executable into the existing Herdr.app bundle and retain its
icon/application identity:

    python3 contrib/desktop/install.py --herdr-binary /absolute/path/to/herdr --macos-executable <native-cache>/herdr-desktop/build/release/HerdrDesktop

Open Herdr.app. The installer signs the updated local bundle ad hoc; it does not
publish a signed distribution. Keep a backup of the old bundle/executable.

## Verification

    python3 -m unittest discover -s contrib/desktop/tests -p 'test_*.py' -v

Native acceptance uses tests/osc_probe.py as a disposable client binary, never
an existing agent pane. Set DESKTOP_PROBE_OUTPUT to a result file and point a
temporary desktop profile's herdr_binary to the executable probe. Launch the
desktop host with --profile and --snapshot. The probe queries OSC 10/11 and all
16 OSC 4 entries, then prints a color sample and exits. Snapshot capture draws
only the application's own widget tree; it needs no desktop screen-recording
permission. Linux acceptance uses a separate --application-id.

Compare the replies with the profile, inspect the image, and verify production
server PIDs, terminal/agent IDs and machine-profile IDs before and after
refreshing the production UI. Preserve SSH_AUTH_SOCK; inherited Herdr routing
overrides, foreign terminal identifiers and color overrides are removed only
from the UI child's environment.

Desktop profile changes go through the installer so the Herdr UI palette and
native terminal palette stay aligned. These hosts do not copy session files
between devices.

References:
- [Herdr theme configuration](https://herdr.dev/tui/docs/configuration/)
- [Herdr remote machines](https://herdr.dev/tui/docs/connecting-machines/)
- [VTE terminal API](https://gnome.pages.gitlab.gnome.org/vte/gtk3/class.Terminal.html)
- [SwiftTerm](https://github.com/migueldeicaza/SwiftTerm)

The full Linux input/lifecycle acceptance uses an existing Herdr runtime:

    xvfb-run -a python3 contrib/desktop/tests/test_herdr_input_linux.py --herdr-binary /absolute/path/to/herdr

It creates a unique named session with isolated XDG config/state/runtime, a
private D-Bus without service activation, and a fake raw-input application.
Real X11 keyboard/mouse events go through the desktop host and Herdr. The test
requires immediate keystrokes without Enter, no mouse-coordinate text, and the
same program PID/raw tty after closing and reopening the GUI. It stops and
deletes only its own session. Normal user sessions and LLMs are never addressed.

If a foreground interactive CLI unexpectedly buffers keys and echoes mouse
reports, inspect that pane's tty flags before changing the desktop. ECHO/ICANON
may be enabled while an interactive CLI expects raw input. A repair must identify the actual foreground process and
its exact tty, restore the application's expected input flags, and preserve
the process and session. Do not reset every tty, restart the shared server, or
send shell repair commands into an agent's input.
