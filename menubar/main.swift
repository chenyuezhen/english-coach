import Cocoa
import Carbon.HIToolbox

/// 服务地址。优先读环境变量，其次读本地配置文件，最后才用默认值。
/// 仓库里不写死真实地址，换服务器改 ~/.english-coach/api 即可。
func apiBase() -> String {
    if let v = ProcessInfo.processInfo.environment["ENGLISH_COACH_API"], !v.isEmpty {
        return v.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    let path = NSHomeDirectory() + "/.english-coach/api"
    if let s = try? String(contentsOfFile: path, encoding: .utf8) {
        let v = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if !v.isEmpty { return v }
    }
    return "http://127.0.0.1:8080"
}

let KEY_PATH = NSHomeDirectory() + "/.english-coach/key"
let LOG_PATH = NSHomeDirectory() + "/.english-coach/app.log"
let HOTKEY_CODE = UInt32(kVK_ANSI_E)
let HOTKEY_MODS = UInt32(controlKey | optionKey)

func log(_ msg: String) {
    let f = "[" + ISO8601DateFormatter().string(from: Date()) + "] " + msg + "\n"
    if let h = FileHandle(forWritingAtPath: LOG_PATH) {
        h.seekToEndOfFile()
        h.write(f.data(using: .utf8)!)
        h.closeFile()
    } else {
        try? f.write(toFile: LOG_PATH, atomically: true, encoding: .utf8)
    }
}

func readAPIKey() -> String {
    if let k = ProcessInfo.processInfo.environment["ENGLISH_COACH_KEY"], !k.isEmpty { return k }
    if let s = try? String(contentsOfFile: KEY_PATH, encoding: .utf8) {
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    return ""
}

/// 从 App bundle 里加载菜单栏图标
func loadMenubarIcon() -> NSImage? {
    let bundle = Bundle.main
    if let path = bundle.path(forResource: "menubarTemplate", ofType: "png"),
       let img = NSImage(contentsOfFile: path) {
        img.size = NSSize(width: 18, height: 18)
        return img
    }
    if let path = bundle.path(forResource: "menubarTemplate@2x", ofType: "png"),
       let img = NSImage(contentsOfFile: path) {
        img.size = NSSize(width: 18, height: 18)
        return img
    }
    return nil
}

/// Electron 类应用（Trae / VS Code / Codex 桌面端）默认把 Accessibility 树关着，
/// 读 focusedUIElement 会返回 -25212（kAXErrorAPIDisabled），只能退回模拟 ⌘C。
/// 设置 AXManualAccessibility 可以把树打开，之后就能直读选区、不碰剪贴板。
func enableAXTree(for app: NSRunningApplication) {
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(axApp, 1.0)
    let r = AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    log("ax: enable AXManualAccessibility for \(app.localizedName ?? "?") r=\(r.rawValue)")
}

/// 用 Accessibility API 直接读前台 App 的选中文字（首选，不碰剪贴板）
func axSelectedText() -> String? {
    guard AXIsProcessTrusted() else { return nil }
    guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(axApp, 1.0)

    var focused: AnyObject?
    var err = AXUIElementCopyAttributeValue(axApp, kAXFocusedUIElementAttribute as CFString, &focused)

    // Trae / VS Code / Codex 桌面端是 Electron：默认不暴露 AX 树，
    // 读 focusedUIElement 会返回 -25212（kAXErrorNoValue）。
    // 用 AXManualAccessibility 打开后再读一次。
    if err != .success {
        enableAXTree(for: app)
        usleep(150_000)
        err = AXUIElementCopyAttributeValue(axApp, kAXFocusedUIElementAttribute as CFString, &focused)
    }

    guard err == .success, let el = focused else {
        log("ax: focusedUIElement err=\(err.rawValue) app=\(app.localizedName ?? "?")")
        return nil
    }
    let elem = el as! AXUIElement

    // 选区可能在文本框里，也可能挂在文档/网页上；沿父链找几层
    var cur: AXUIElement? = elem
    for _ in 0..<6 {
        guard let e = cur else { break }

        // 首选：用 AXSelectedTextRange + AXValue 截取。
        // 只认 range.length > 0，才能排除「输入框把占位文字当 selectedText 返回」的情况
        // （ChatGPT / Codex 桌面端就是这样，光看 AXSelectedText 会拿到「随心输入」）。
        var rv: AnyObject?
        if AXUIElementCopyAttributeValue(e, kAXSelectedTextRangeAttribute as CFString, &rv) == .success,
           let rangeVal = rv {
            var range = CFRange()
            if AXValueGetValue(rangeVal as! AXValue, .cfRange, &range), range.length > 0 {
                var vv: AnyObject?
                if AXUIElementCopyAttributeValue(e, kAXValueAttribute as CFString, &vv) == .success,
                   let value = vv as? String {
                    let chars = Array(value)
                    let lo = min(max(range.location, 0), chars.count)
                    let hi = min(lo + range.length, chars.count)
                    if hi > lo {
                        let s = String(chars[lo..<hi])
                        log("ax: range hit len=\(s.count)")
                        return s
                    }
                }
            }
        }

        // 次选：直接读 AXSelectedText（原生 App 多数只支持这个）
        var v: AnyObject?
        if AXUIElementCopyAttributeValue(e, kAXSelectedTextAttribute as CFString, &v) == .success,
           let s = v as? String, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            log("ax: selectedText hit len=\(s.count)")
            return s
        }
        var parent: AnyObject?
        guard AXUIElementCopyAttributeValue(e, kAXParentAttribute as CFString, &parent) == .success,
              let p = parent else { break }
        cur = (p as! AXUIElement)
    }
    log("ax: no selection found")
    return nil
}

/// 取当前选中的文字：优先 AX 直读（不碰剪贴板），失败再模拟 ⌘C。
func grabSelectedText() -> (String, Bool) {
    if let s = axSelectedText() {
        log("grabSelectedText: ax hit len=\(s.count)")
        return (s, true)
    }

    let pb = NSPasteboard.general
    let saved = pb.pasteboardItems?.first?.string(forType: .string)

    guard AXIsProcessTrusted() else {
        log("grabSelectedText: no AX permission, using clipboard fallback")
        return (saved?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "", false)
    }

    // 等 ⌃⌥ 松开：模拟的 ⌘C 若叠加用户仍按着的修饰键会变成 ⌃⌥⌘C，目标 App 不认
    for _ in 0..<20 {
        let f = CGEventSource.flagsState(.combinedSessionState)
        if !f.contains(.maskControl) && !f.contains(.maskAlternate) { break }
        usleep(50_000)
    }

    // 用唯一哨兵值判断是否真的复制到了东西
    let sentinel = "__EC_SENTINEL_\(UUID().uuidString)__"
    pb.clearContents()
    pb.setString(sentinel, forType: .string)

    let src = CGEventSource(stateID: .combinedSessionState)
    let before = pb.changeCount
    if let d = CGEvent(keyboardEventSource: src, virtualKey: 0x08, keyDown: true) {
        d.flags = .maskCommand
        d.post(tap: .cghidEventTap)
    }
    if let u = CGEvent(keyboardEventSource: src, virtualKey: 0x08, keyDown: false) {
        u.flags = .maskCommand
        u.post(tap: .cghidEventTap)
    }

    // 轮询剪贴板变化：Electron 处理 ⌘C 比原生 App 慢，固定 sleep 会读不到
    var waited = 0
    while waited < 1_200 {
        if pb.changeCount != before { break }
        usleep(30_000)
        waited += 30
    }
    usleep(60_000)

    let grabbed = pb.string(forType: .string) ?? ""
    let gotNew = grabbed != sentinel && !grabbed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    log("grabSelectedText: copy waited=\(waited)ms gotLen=\(grabbed.count)")

    // 恢复原剪贴板
    if let s = saved {
        pb.clearContents()
        pb.setString(s, forType: .string)
    } else {
        pb.clearContents()
    }

    if gotNew {
        return (grabbed, true)
    }

    log("grabSelectedText: nothing copied")
    return ("", false)
}

/// 用 curl 子进程走 HTTP 请求。
/// 原因：ad-hoc 签名的 App 即使写了 NSAllowsArbitraryLoads，ATS 仍会拦 http://。
/// ATS 只作用于 URLSession / NSURLConnection，不限制子进程。
func postReport(selected: String, intent: String, app: String, completion: @escaping (Result<[String: Any], Error>) -> Void) {
    let body: [String: Any] = ["selected": selected, "intent_zh": intent, "app": app]
    guard let bodyData = try? JSONSerialization.data(withJSONObject: body) else {
        completion(.failure(NSError(domain: "ec", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "请求体编码失败"])))
        return
    }

    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
    p.arguments = [
        "-sS", "-m", "120", "-X", "POST", apiBase() + "/api/report",
        "-H", "Content-Type: application/json",
        "-H", "X-API-Key: \(readAPIKey())",
        "--data-binary", "@-",
    ]

    let inPipe = Pipe(), outPipe = Pipe()
    p.standardInput = inPipe
    p.standardOutput = outPipe
    p.standardError = outPipe

    do {
        try p.run()
    } catch {
        log("postReport spawn failed: \(error.localizedDescription)")
        completion(.failure(error))
        return
    }

    inPipe.fileHandleForWriting.write(bodyData)
    try? inPipe.fileHandleForWriting.close()

    DispatchQueue.global().async {
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let raw = String(data: data, encoding: .utf8) ?? ""

        DispatchQueue.main.async {
            log("postReport exit=\(p.terminationStatus) bytes=\(data.count)")
            guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                log("postReport parse fail: \(raw.prefix(200))")
                completion(.failure(NSError(domain: "ec", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "响应解析失败：\(raw.prefix(150))"])))
                return
            }
            if let e = obj["error"] as? String {
                completion(.failure(NSError(domain: "ec", code: 2,
                    userInfo: [NSLocalizedDescriptionKey: e])))
                return
            }
            completion(.success(obj))
        }
    }
}

func frontmostAppName() -> String {
    guard let app = NSWorkspace.shared.frontmostApplication else { return "" }
    return app.localizedName ?? ""
}

// MARK: - 面板

final class EntryPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var hotKeyRef: EventHotKeyRef?
    var panel: EntryPanel?
    var stack: NSStackView!
    var intentField: NSTextField!
    var selectedText = ""
    var isLoading = false
    /// 按下热键时位于最前的 App，关窗后把焦点还回去
    var previousApp: NSRunningApplication?

    func applicationDidFinishLaunching(_ n: Notification) {
        log("=== launched, pid=\(ProcessInfo.processInfo.processIdentifier)")
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            if let img = loadMenubarIcon() {
                img.isTemplate = true
                b.image = img
                b.imagePosition = .imageOnly
                b.imageScaling = .scaleProportionallyDown
            } else {
                b.title = "EC"
                b.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .semibold)
            }
        }
        rebuildMenu()
        registerHotKey()
        checkAccessibility()
        log("key=\(readAPIKey().prefix(10)) axTrusted=\(AXIsProcessTrusted())")

        // 兜底：非激活面板里 NSTextField 的 action 不一定发出，这里处理回车提交 / ESC 关闭。
        // 只在「填写意图」这一屏生效，结果屏交给按钮的 keyEquivalent 处理。
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] ev in
            guard let self = self, let p = self.panel, p.isVisible,
                  let f = self.intentField, f.window != nil else { return ev }
            if ev.keyCode == 36 {
                self.submitEntry()
                return nil
            }
            if ev.keyCode == 53 {
                self.closePanel()
                return nil
            }
            return ev
        }

        if ProcessInfo.processInfo.environment["EC_SELFTEST"] == "1" {
            log("selftest mode: firing trigger in 2s")
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.selectedText = "please kindly confirm this ASAP"
                self?.showEntryPanel()
                log("selftest panel shown, window visible=\(self?.panel?.isVisible ?? false)")
            }
        }

        if ProcessInfo.processInfo.environment["EC_SUBMIT_TEST"] == "1" {
            log("submit test: posting directly")
            postReport(selected: "please kindly confirm this ASAP", intent: "催老板但要客气", app: "Selftest") { r in
                switch r {
                case .success(let o): log("submit test OK topic=\(o["topic"] ?? "")")
                case .failure(let e): log("submit test FAIL \(e.localizedDescription)")
                }
            }
        }
    }

    func rebuildMenu() {
        let k = readAPIKey()
        let menu = NSMenu()
        menu.addItem(withTitle: "记一笔  ⌃⌥E", action: #selector(trigger), keyEquivalent: "").target = self
        menu.addItem(.separator())
        let keyItem = NSMenuItem(title: k.isEmpty ? "未配置 API Key" : "API Key: \(k.prefix(10))…",
                                 action: nil, keyEquivalent: "")
        keyItem.isEnabled = false
        menu.addItem(keyItem)
        menu.addItem(withTitle: "设置 API Key…", action: #selector(setKey), keyEquivalent: "").target = self
        menu.addItem(withTitle: "打开网页", action: #selector(openWeb), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = menu
    }

    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        let handlerStatus = InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let p = userData else { return noErr }
            let d = Unmanaged<AppDelegate>.fromOpaque(p).takeUnretainedValue()
            // 延迟一点再处理：热键是 keyDown 触发的，此时 ⌃⌥ 还按着，
            // 立刻模拟 ⌘C 会变成 ⌃⌥⌘C，目标 App 不认这个组合。
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { d.trigger() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), nil)
        log("InstallEventHandler status=\(handlerStatus)")

        let hid = EventHotKeyID(signature: OSType(0x45434F41), id: 1)
        let st = RegisterEventHotKey(HOTKEY_CODE, HOTKEY_MODS, hid, GetApplicationEventTarget(), 0, &hotKeyRef)
        log("RegisterEventHotKey status=\(st) ref=\(hotKeyRef != nil)")
    }

    func checkAccessibility() {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(opts)
    }

    @objc func openWeb() {
        NSWorkspace.shared.open(URL(string: apiBase())!)
    }

    @objc func setKey() {
        let alert = NSAlert()
        alert.messageText = "设置 API Key"
        alert.informativeText = "在网页「成员」页点 API Key 即可复制。"
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        tf.stringValue = readAPIKey()
        alert.accessoryView = tf
        alert.addButton(withTitle: "保存")
        alert.addButton(withTitle: "取消")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            let v = tf.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            try? FileManager.default.createDirectory(atPath: NSHomeDirectory() + "/.english-coach",
                                                    withIntermediateDirectories: true)
            try? v.write(toFile: KEY_PATH, atomically: true, encoding: .utf8)
            rebuildMenu()
        }
    }

    @objc func trigger() {
        log("trigger fired, isLoading=\(isLoading)")
        guard !isLoading else { return }
        if readAPIKey().isEmpty {
            setKey()
            return
        }
        previousApp = NSWorkspace.shared.frontmostApplication
        let (text, got) = grabSelectedText()
        selectedText = text
        log("selected len=\(text.count) fresh=\(got) trusted=\(AXIsProcessTrusted())")
        showEntryPanel(hint: got ? nil : (text.isEmpty
            ? "没读到选中文字，可以直接写中文意图"
            : "读不到选区，用的是剪贴板内容"))
    }

    // MARK: 面板

    func makePanel() -> EntryPanel {
        if let p = panel { return p }
        // .nonactivatingPanel：即使 App 没被激活，面板也能拿到键盘焦点。
        // 这是 Alfred / Raycast 这类工具的标准做法，绕开 macOS 14+ 弱化 activate 的问题。
        let p = EntryPanel(contentRect: NSRect(x: 0, y: 0, width: 520, height: 200),
                           styleMask: [.titled, .closable, .fullSizeContentView, .nonactivatingPanel],
                           backing: .buffered, defer: false)
        p.titlebarAppearsTransparent = true
        p.titleVisibility = .hidden
        p.isMovableByWindowBackground = true
        p.level = .floating
        p.backgroundColor = NSColor.windowBackgroundColor
        // 关键：允许在任意桌面空间和全屏应用之上显示，
        // 否则在 Trae 全屏 / 另一个 Space 里按快捷键时，窗口会出现在看不到的地方。
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        p.hidesOnDeactivate = false
        p.contentView = NSView()

        let sv = NSStackView()
        sv.orientation = .vertical
        sv.alignment = .leading
        sv.spacing = 10
        sv.edgeInsets = NSEdgeInsets(top: 18, left: 22, bottom: 18, right: 22)
        sv.translatesAutoresizingMaskIntoConstraints = false
        p.contentView!.addSubview(sv)
        NSLayoutConstraint.activate([
            sv.topAnchor.constraint(equalTo: p.contentView!.topAnchor, constant: 22),
            sv.leadingAnchor.constraint(equalTo: p.contentView!.leadingAnchor),
            sv.trailingAnchor.constraint(equalTo: p.contentView!.trailingAnchor),
            sv.bottomAnchor.constraint(lessThanOrEqualTo: p.contentView!.bottomAnchor, constant: -16),
        ])
        self.stack = sv
        self.panel = p
        return p
    }

    func clearStack() {
        for v in stack.arrangedSubviews {
            stack.removeArrangedSubview(v)
            v.removeFromSuperview()
        }
    }

    func showEntryPanel(hint: String? = nil) {
        let p = makePanel()
        clearStack()

        let title = NSTextField(labelWithString: "记一笔")
        title.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        stack.addArrangedSubview(title)

        if !selectedText.isEmpty {
            let s = NSTextField(wrappingLabelWithString: selectedText)
            s.font = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
            s.textColor = .secondaryLabelColor
            s.maximumNumberOfLines = 3
            s.preferredMaxLayoutWidth = 470
            stack.addArrangedSubview(s)
            s.widthAnchor.constraint(equalToConstant: 470).isActive = true
        } else {
            let msg = hint ?? "未选中文字，只填意图也可以"
            let s = NSTextField(wrappingLabelWithString: msg)
            s.font = NSFont.systemFont(ofSize: 11.5)
            s.textColor = .tertiaryLabelColor
            s.preferredMaxLayoutWidth = 470
            stack.addArrangedSubview(s)
            s.widthAnchor.constraint(equalToConstant: 470).isActive = true
        }

        let field = NSTextField()
        field.placeholderString = "你想表达什么？（中文，可留空）"
        field.font = NSFont.systemFont(ofSize: 13)
        field.target = self
        field.action = #selector(submitEntry)
        field.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(field)
        field.widthAnchor.constraint(equalToConstant: 476).isActive = true
        field.heightAnchor.constraint(equalToConstant: 26).isActive = true
        self.intentField = field

        let row = NSStackView()
        row.orientation = .horizontal
        row.spacing = 8
        let cancel = NSButton(title: "取消", target: self, action: #selector(closePanel))
        cancel.keyEquivalent = "\u{1b}"
        let submit = NSButton(title: "提交", target: self, action: #selector(submitEntry))
        submit.keyEquivalent = "\r"
        submit.bezelStyle = .rounded
        row.addArrangedSubview(NSView())
        row.addArrangedSubview(cancel)
        row.addArrangedSubview(submit)
        stack.addArrangedSubview(row)
        row.widthAnchor.constraint(equalToConstant: 476).isActive = true

        centerAndShow(p, height: selectedText.isEmpty ? 170 : 210)
        p.makeFirstResponder(field)
    }

    func centerAndShow(_ p: NSPanel, height: CGFloat) {
        p.setContentSize(NSSize(width: 520, height: height))
        // 用鼠标所在屏，避免多显示器时弹到另一块屏幕
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSScreen.main ?? NSScreen.screens.first
        if let f = screen?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: f.midX - 260, y: f.midY - height / 2 + 60))
        }

        p.becomesKeyOnlyIfNeeded = false
        p.level = .floating
        p.orderFrontRegardless()

        // macOS 14+ 的协作式激活会让后台 App 的 activate 被忽略，
        // 表现为面板 visible=true 但收不到键盘事件、前台仍是别的 App。
        // 可靠做法：临时切成 .regular 再 activate，拿到前台后再切回 .accessory。
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        p.makeKeyAndOrderFront(nil)
        p.makeKey()
        if let f = self.intentField { p.makeFirstResponder(f) }
        log("panel shown visible=\(p.isVisible) key=\(p.isKeyWindow)")

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            guard let self = self else { return }
            p.makeKey()
            if let f = self.intentField { p.makeFirstResponder(f) }
            log("focus final key=\(p.isKeyWindow) appActive=\(NSApp.isActive) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?") responder=\(type(of: p.firstResponder ?? NSView()))")
        }
    }

    /// 关闭面板后交还前台，并恢复成无 Dock 图标的菜单栏 App
    func dismissPanel() {
        panel?.orderOut(nil)
        let prev = previousApp
        previousApp = nil
        NSApp.setActivationPolicy(.accessory)
        guard let p = prev, !p.isTerminated else { return }

        // macOS 14+ 的后台 App 激活是「协作式」的：刚切完 activationPolicy 立刻
        // activate 经常被忽略，所以延后一点再激活，并在没切回去时重试一次。
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            p.activate(options: [.activateAllWindows])
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                let stillMe = NSWorkspace.shared.frontmostApplication?.processIdentifier
                    == ProcessInfo.processInfo.processIdentifier
                if stillMe {
                    p.activate(options: [.activateAllWindows])
                    log("dismissPanel: retry activate \(p.localizedName ?? "?")")
                } else {
                    log("dismissPanel: back to \(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")")
                }
            }
        }
    }

    @objc func closePanel() {
        dismissPanel()
    }

    @objc func submitEntry() {
        guard !isLoading else { return }
        let intent = intentField?.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        log("submitEntry intent='\(intent)' selected='\(selectedText.prefix(30))'")
        if intent.isEmpty && selectedText.isEmpty {
            NSSound.beep()
            return
        }
        isLoading = true
        showLoading()
        let app = frontmostAppName()
        postReport(selected: selectedText, intent: intent, app: app) { [weak self] result in
            guard let self = self else { return }
            self.isLoading = false
            switch result {
            case .success(let obj): self.showResult(obj)
            case .failure(let err): self.showError(err.localizedDescription)
            }
        }
    }

    func showLoading() {
        clearStack()
        let t = NSTextField(labelWithString: "分析中…")
        t.font = NSFont.systemFont(ofSize: 13)
        t.textColor = .secondaryLabelColor
        stack.addArrangedSubview(t)
        panel?.setContentSize(NSSize(width: 520, height: 90))
    }

    func showError(_ msg: String) {
        clearStack()
        let t = NSTextField(wrappingLabelWithString: "提交失败：\(msg)")
        t.font = NSFont.systemFont(ofSize: 12.5)
        t.textColor = .systemRed
        t.preferredMaxLayoutWidth = 470
        stack.addArrangedSubview(t)
        t.widthAnchor.constraint(equalToConstant: 470).isActive = true
        let row = NSStackView()
        row.orientation = .horizontal
        row.addArrangedSubview(NSView())
        row.addArrangedSubview(NSButton(title: "关闭", target: self, action: #selector(closePanel)))
        stack.addArrangedSubview(row)
        row.widthAnchor.constraint(equalToConstant: 476).isActive = true
        panel?.setContentSize(NSSize(width: 520, height: 150))
    }

    func showResult(_ obj: [String: Any]) {
        clearStack()
        var h: CGFloat = 0

        let topic = NSTextField(labelWithString: (obj["topic"] as? String) ?? "")
        topic.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        stack.addArrangedSubview(topic)
        h += 26

        if let why = obj["why_wrong"] as? String, !why.isEmpty {
            let w = NSTextField(wrappingLabelWithString: why)
            w.font = NSFont.systemFont(ofSize: 12.5)
            w.textColor = .secondaryLabelColor
            w.maximumNumberOfLines = 3
            w.preferredMaxLayoutWidth = 470
            stack.addArrangedSubview(w)
            w.widthAnchor.constraint(equalToConstant: 470).isActive = true
            h += 44
        }

        let sep = NSBox()
        sep.boxType = .separator
        stack.addArrangedSubview(sep)
        sep.widthAnchor.constraint(equalToConstant: 476).isActive = true
        h += 14

        let levels = (obj["levels"] as? [String: String]) ?? [:]
        let order: [(String, String)] = [("平级", "peer"), ("对上级", "manager"), ("正式邮件", "formal_email")]
        var best = ""
        for (label, key) in order {
            guard let text = levels[key], !text.isEmpty else { continue }
            if best.isEmpty { best = text }
            let btn = NSButton(title: text, target: self, action: #selector(copyLevel(_:)))
            btn.bezelStyle = .inline
            btn.alignment = .left
            btn.font = NSFont.systemFont(ofSize: 12.5)
            btn.contentTintColor = .labelColor
            btn.toolTip = "点击复制 · \(label)"
            btn.translatesAutoresizingMaskIntoConstraints = false
            stack.addArrangedSubview(btn)
            btn.widthAnchor.constraint(equalToConstant: 476).isActive = true
            h += 26
        }

        let row = NSStackView()
        row.orientation = .horizontal
        row.spacing = 8
        let copyBtn = NSButton(title: "复制并关闭", target: self, action: #selector(copyBestAndClose))
        copyBtn.keyEquivalent = "\r"
        copyBtn.bezelStyle = .rounded
        let close = NSButton(title: "关闭", target: self, action: #selector(closePanel))
        row.addArrangedSubview(NSView())
        row.addArrangedSubview(close)
        row.addArrangedSubview(copyBtn)
        stack.addArrangedSubview(row)
        row.widthAnchor.constraint(equalToConstant: 476).isActive = true
        h += 40

        self.lastBest = best
        panel?.setContentSize(NSSize(width: 520, height: min(h + 60, 480)))
    }

    var lastBest = ""

    @objc func copyLevel(_ sender: NSButton) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(sender.title, forType: .string)
        dismissPanel()
    }

    @objc func copyBestAndClose() {
        if !lastBest.isEmpty {
            let pb = NSPasteboard.general
            pb.clearContents()
            pb.setString(lastBest, forType: .string)
        }
        dismissPanel()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()