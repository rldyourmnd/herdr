import Cocoa
import SwiftTerm

struct TerminalColors: Decodable {
    let background, foreground, cursor: String
    let selection_background, selection_foreground: String
    let palette: [String]
}
struct DesktopProfile: Decodable {
    let format: Int
    let herdr_binary: String
    let ui_colors: [String: String]
    let terminal: TerminalColors
}
enum DesktopError: Error { case invalidProfile }

func components(_ hex: String) throws -> (UInt16, UInt16, UInt16) {
    guard hex.count == 7, hex.first == "#",
          let value = UInt32(hex.dropFirst(), radix: 16) else {
        throw DesktopError.invalidProfile
    }
    return (UInt16((value >> 16) & 255), UInt16((value >> 8) & 255), UInt16(value & 255))
}
func nativeColor(_ hex: String) throws -> NSColor {
    let (r, g, b) = try components(hex)
    return NSColor(srgbRed: Double(r)/255, green: Double(g)/255,
                   blue: Double(b)/255, alpha: 1)
}
func ansiColor(_ hex: String) throws -> SwiftTerm.Color {
    let (r, g, b) = try components(hex)
    return SwiftTerm.Color(red8: r, green8: g, blue8: b)
}
func option(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name),
          CommandLine.arguments.indices.contains(index + 1) else { return nil }
    return CommandLine.arguments[index + 1]
}

@MainActor
final class DesktopBackground: NSView {
    let color: NSColor
    init(frame: NSRect, color: NSColor) {
        self.color = color
        super.init(frame: frame)
    }
    required init?(coder: NSCoder) { return nil }
    override var isOpaque: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        color.setFill()
        dirtyRect.fill()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate,
                         NSWindowDelegate, LocalProcessTerminalViewDelegate {
    var window: NSWindow?
    var terminal: LocalProcessTerminalView?
    var closing = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        do { try start() } catch {
            let alert = NSAlert()
            alert.messageText = "Herdr could not start"
            alert.informativeText = "Check the desktop profile and Herdr binary: \(error)"
            alert.alertStyle = .critical
            alert.runModal()
            NSApp.terminate(nil)
        }
    }

    func start() throws {
        let path = option("--profile") ??
            NSHomeDirectory() + "/.config/herdr/desktop.json"
        let profile = try JSONDecoder().decode(
            DesktopProfile.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        let c = profile.terminal
        guard profile.format == 1, c.palette.count == 16,
              profile.ui_colors.count == 19,
              profile.ui_colors["panel_bg"] == c.background,
              profile.ui_colors["text"] == c.foreground,
              profile.herdr_binary.hasPrefix("/"),
              FileManager.default.isExecutableFile(atPath: profile.herdr_binary)
        else { throw DesktopError.invalidProfile }
        // Validate the whole palette before creating a partially styled window.
        for color in Array(profile.ui_colors.values) + c.palette +
            [c.background, c.foreground, c.cursor,
             c.selection_background, c.selection_foreground] {
            _ = try components(color)
        }
        let bg = try nativeColor(c.background)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1420, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        self.window = window
        window.delegate = self
        window.title = "Herdr"
        window.appearance = NSAppearance(named: .aqua)
        window.backgroundColor = bg
        window.isOpaque = true
        window.titlebarAppearsTransparent = true
        window.center()
        window.contentView = DesktopBackground(frame: window.contentView!.bounds, color: bg)

        let terminal = LocalProcessTerminalView(frame: window.contentView!.bounds)
        self.terminal = terminal
        terminal.autoresizingMask = [.width, .height]
        terminal.appearance = NSAppearance(named: .aqua)
        terminal.nativeBackgroundColor = bg
        terminal.nativeForegroundColor = try nativeColor(c.foreground)
        terminal.installColors(try c.palette.map(ansiColor))
        terminal.selectedTextBackgroundColor = try nativeColor(c.selection_background)
        terminal.selectedTextForegroundColor = try nativeColor(c.selection_foreground)
        terminal.caretColor = try nativeColor(c.cursor)
        terminal.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        terminal.processDelegate = self
        window.contentView!.addSubview(terminal)
        installMenu()
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(terminal)

        let inheritedTerminalKeys: Set<String> = [
            "TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
            "TERMINFO", "TERMINFO_DIRS", "COLORFGBG", "NO_COLOR", "CLICOLOR",
            "CLICOLOR_FORCE", "VTE_VERSION", "KITTY_WINDOW_ID", "WEZTERM_PANE",
            "ITERM_SESSION_ID", "ALACRITTY_SOCKET", "ALACRITTY_WINDOW_ID",
            "GNOME_TERMINAL_SERVICE", "GNOME_TERMINAL_SCREEN", "TERMINAL_EMULATOR",
            "TMUX", "TMUX_PANE", "STY", "WINDOWID", "SSH_TTY"
        ]
        var env = ProcessInfo.processInfo.environment.filter {
            !$0.key.hasPrefix("HERDR_") && !inheritedTerminalKeys.contains($0.key)
        }
        env["PATH"] = NSHomeDirectory() +
            "/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:" +
            (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["TERM_PROGRAM"] = "herdr-desktop"
        env["TERM_PROGRAM_VERSION"] = "0.1.0"
        FileManager.default.changeCurrentDirectoryPath(NSHomeDirectory())
        terminal.startProcess(executable: profile.herdr_binary, args: [],
                              environment: env.map { "\($0.key)=\($0.value)" })
        if let snapshot = option("--snapshot") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.snapshot(snapshot)
            }
        }
    }

    func installMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Herdr",
                       action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        bar.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All",
                     action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        bar.addItem(editItem)
        NSApp.mainMenu = bar
    }

    func snapshot(_ path: String) {
        guard let view = window?.contentView,
              let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        do {
            // SwiftTerm omits default-background pixels from cacheDisplay.
            // Composite over our actual NSWindow background, as WindowServer does.
            guard let image = bitmap.cgImage,
                  let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let canvas = CGContext(data: nil, width: image.width, height: image.height,
                      bitsPerComponent: 8, bytesPerRow: 0, space: space,
                      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue),
                  let background = window?.backgroundColor else { return }
            let bounds = CGRect(x: 0, y: 0, width: image.width, height: image.height)
            canvas.setFillColor(background.cgColor)
            canvas.fill(bounds)
            canvas.draw(image, in: bounds)
            guard let opaque = canvas.makeImage(),
                  let png = NSBitmapImageRep(cgImage: opaque)
                      .representation(using: .png, properties: [:]) else { return }
            try png.write(to: URL(fileURLWithPath: path), options: .atomic)
        } catch { NSLog("Herdr snapshot failed: %@", String(describing: error)) }
    }
    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
    func processTerminated(source: TerminalView, exitCode: Int32?) {
        if !closing { NSApp.terminate(nil) }
    }
    func windowWillClose(_ notification: Notification) {
        closing = true
        terminal?.terminate() // Only this application's direct TUI child.
    }
    func applicationWillTerminate(_ notification: Notification) {
        closing = true
        terminal?.terminate()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.activate(ignoringOtherApps: true)
    app.run()
}
