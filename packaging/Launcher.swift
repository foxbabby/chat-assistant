import AppKit
import WebKit
import ApplicationServices

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var worker: Process?
    var setup: Process?
    var timer: Timer?
    var attempts = 0
    var quitting = false
    var lastPermissionRequest = Date.distantPast

    func applicationDidFinishLaunching(_ notification: Notification) {
        let frame = NSRect(x: 0, y: 0, width: 1140, height: 840)
        window = NSWindow(contentRect: frame, styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "聊天助手"
        window.minSize = NSSize(width: 620, height: 660)
        window.delegate = self
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "accessibilityPermission")
        webView = WKWebView(frame: frame, configuration: config)
        webView.navigationDelegate = self
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        webView.loadHTMLString("<meta charset='utf-8'><body style='background:#f3f6f3;color:#247457;font:18px -apple-system;text-align:center;padding-top:25%'>正在打开聊天助手…</body>", baseURL: nil)
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "退出聊天助手", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let edit = NSMenu(title: "编辑")
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        prepare()
    }

    func fail(_ message: String) {
        guard !quitting else { return }
        timer?.invalidate()
        let alert = NSAlert()
        alert.messageText = "聊天助手暂时无法启动"
        alert.informativeText = message
        alert.addButton(withTitle: "退出")
        alert.runModal()
        NSApp.terminate(nil)
    }

    func prepare() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let uv = [home + "/.local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"].first { FileManager.default.isExecutableFile(atPath: $0) }
        guard let uv = uv, let resources = Bundle.main.resourcePath else {
            fail("请先安装 uv，然后重新打开应用。")
            return
        }
        let venv = home + "/Library/Application Support/微信聊天助手/venv"
        let process = Process()
        process.executableURL = URL(fileURLWithPath: uv)
        process.arguments = ["sync", "--frozen", "--python", "3.12", "--project", resources + "/app", "--quiet"]
        var environment = ProcessInfo.processInfo.environment
        environment["UV_PROJECT_ENVIRONMENT"] = venv
        process.environment = environment
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self, !self.quitting else { return }
                if result.terminationStatus != 0 {
                    self.fail("运行环境准备失败，请检查网络；可使用源码目录中的 start.command 查看安装结果。")
                } else {
                    self.launchWorker(venv + "/bin/python", resources + "/app/src/server.py")
                }
            }
        }
        setup = process
        do { try process.run() } catch { fail("无法启动运行环境。") }
    }

    func launchWorker(_ python: String, _ script: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.fail("后台服务已停止，或另一个助手已在运行。请关闭重复实例后重新打开。")
            }
        }
        worker = process
        do { try process.run() } catch { fail("无法启动后台服务。"); return }
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.checkReady() }
    }

    func checkReady() {
        guard let worker = worker, worker.isRunning else { return }
        attempts += 1
        if attempts > 80 { fail("启动超时，请确认本机 18766 端口未被占用。"); return }
        let url = URL(string: "http://127.0.0.1:18766/")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 0.3
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return }
            DispatchQueue.main.async {
                guard let self = self, self.timer != nil, !self.quitting, self.worker?.isRunning == true else { return }
                self.timer?.invalidate()
                self.timer = nil
                self.webView.load(URLRequest(url: url))
            }
        }.resume()
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        decisionHandler(url?.absoluteString == "about:blank" || (url?.host == "127.0.0.1" && url?.port == 18766) ? .allow : .cancel)
    }
    // Only the trusted, top-level local application page may request permission.
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.name == "accessibilityPermission", message.frameInfo.isMainFrame,
              origin.protocol == "http", origin.host == "127.0.0.1", origin.port == 18766,
              let action = message.body as? String else { return }
        if action == "request" {
            guard Date().timeIntervalSince(lastPermissionRequest) > 2 else { return }
            lastPermissionRequest = Date()
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            // Executed in the signed native app, never delegated to the Python worker.
            _ = AXIsProcessTrustedWithOptions(options)
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
                NSWorkspace.shared.open(url)
            }
        } else if action != "status" { return }
        publishAccessibilityStatus()
    }

    func publishAccessibilityStatus() {
        guard let url = webView?.url, url.scheme == "http", url.host == "127.0.0.1", url.port == 18766 else { return }
        let granted = AXIsProcessTrusted() ? "true" : "false"
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-accessibility-status',{detail:{granted:\(granted)}}));", completionHandler: nil)
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        publishAccessibilityStatus()
    }

    func windowWillClose(_ notification: Notification) { NSApp.terminate(nil) }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }
    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        timer?.invalidate()
        if setup?.isRunning == true { setup?.terminate() }
        if worker?.isRunning == true { worker?.terminate(); worker?.waitUntilExit() }
    }
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
