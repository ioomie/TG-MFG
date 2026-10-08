import AppKit
import Foundation

// This adapter only starts the fixed Python helper. URL contents never become commands.
final class LauncherDelegate: NSObject, NSApplicationDelegate {
    override init() {
        super.init()
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(receiveURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    @objc func receiveURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let value = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
              value == "tg-mfg://start" || value == "tg-mfg://start/" else {
            NSApplication.shared.terminate(nil)
            return
        }
        startCore()
    }

    func applicationShouldOpenUntitledFile(_ sender: NSApplication) -> Bool {
        startCore()
        return false
    }

    func startCore() {
        let folder = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/TG-MFG")
        let executable = folder.appendingPathComponent(".venv/bin/python")
        let script = folder.appendingPathComponent("launcher.py")
        guard FileManager.default.fileExists(atPath: executable.path),
              FileManager.default.fileExists(atPath: script.path) else {
            showFailure("Core files are missing. Run the installer again.")
            return
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = ["-B", "-X", "utf8", script.path, "tg-mfg://start"]
        process.currentDirectoryURL = folder
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        do {
            try process.run()
            process.waitUntilExit()
            if process.terminationStatus != 0 {
                let data = output.fileHandleForReading.readDataToEndOfFile()
                showFailure(String(data: data, encoding: .utf8) ?? "Core startup failed. Check startup.log in the installation directory.")
                return
            }
        } catch {
            showFailure("Startup failed. Reinstall the core.")
            return
        }
        NSApplication.shared.terminate(nil)
    }

    func showFailure(_ text: String) {
        let alert = NSAlert()
        alert.messageText = "TG-MFG"
        alert.informativeText = text
        alert.addButton(withTitle: "OK")
        NSApplication.shared.activate(ignoringOtherApps: true)
        alert.runModal()
        NSApplication.shared.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = LauncherDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
