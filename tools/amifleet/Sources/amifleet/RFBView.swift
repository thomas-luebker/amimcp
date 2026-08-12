// RFBView.swift — a native VNC window. Unlike ScreenView (which polls SHOT/HASH
// over the agent), this drives the embedded RFBClient straight to AmiVNC: a real
// RFB stream with incremental updates and live mouse + keyboard. No macOS Screen
// Sharing — amifleet renders the framebuffer itself.

import SwiftUI
import CoreGraphics
import AppKit
import AmigaKit

@MainActor
final class RFBSession: ObservableObject {
    enum Phase: Equatable {
        case checking, needsInstall, installing, starting, streaming, offline(String)
    }
    @Published var phase: Phase = .checking
    @Published var image: CGImage?
    @Published var screenSize = CGSize(width: 640, height: 256)
    @Published var statusLine = "…"
    /// Transient message shown over the screen after a drag-drop transfer.
    @Published var toast: String?
    /// Where a file being dragged over the screen would land ("→ Work:Games").
    @Published var dropTarget: String?
    /// Low-bandwidth BGR233 mode (AmiVNC -a): 1 byte/pixel, 4× less data.
    @Published var fast = false
    /// Last pointer position in Amiga pixels — drawn as an overlay because
    /// AmiVNC doesn't composite the hardware-sprite cursor into the framebuffer.
    @Published var cursor: CGPoint?

    let machine: Machine
    private var client: RFBClient?
    private var loop: Task<Void, Never>?

    init(machine: Machine) { self.machine = machine }

    /// Switch colour depth / bandwidth. Tears down and reconnects on the mode's
    /// port (full colour and fast live on separate ports).
    func setFast(_ on: Bool) {
        guard on != fast, machine.host != "" else { return }
        fast = on
        stop()
        Task { await bringUp() }
    }

    /// Full lifecycle: is AmiVNC installed? → (offer install) → start → stream.
    func start() {
        Task { await bringUp() }
    }

    func stop() {
        loop?.cancel(); loop = nil
        client?.close(); client = nil
    }

    private func bringUp() async {
        phase = .checking; statusLine = "checking AmiVNC on \(machine.host)…"
        if await VNCLauncher.isInstalled(machine.client) {
            await launchAndStream()
        } else {
            phase = .needsInstall
            statusLine = "AmiVNC isn’t installed on \(machine.name)."
        }
    }

    /// Install AmiVNC via amipkg on request, then continue to streaming.
    func installAndStart() {
        Task {
            phase = .installing
            statusLine = "installing AmiVNC via amipkg (downloading from Aminet)…"
            let r = await VNCLauncher.install(machine.client)
            if r.ok {
                await launchAndStream()
            } else {
                phase = .offline(r.message); statusLine = r.message
            }
        }
    }

    func retry() { Task { await bringUp() } }

    private func launchAndStream() async {
        phase = .starting; statusLine = "starting AmiVNC…"
        await VNCLauncher.startServer(machine.client, fast: fast)
        let client = RFBClient(host: machine.host, port: UInt16(VNCLauncher.activePort(fast: fast)))
        self.client = client
        loop = Task.detached(priority: .userInitiated) { [weak self] in
            await self?.run(client: client, password: VNCLauncher.password)
        }
    }

    private nonisolated func run(client: RFBClient, password: String) async {
        do {
            try client.handshake(password: password)
            await MainActor.run {
                self.screenSize = CGSize(width: client.width, height: client.height)
                self.statusLine = "\(client.width)×\(client.height) — \(client.name.trimmingCharacters(in: .whitespaces))"
                self.phase = .streaming
            }
            try client.requestUpdate(incremental: false)
            while !Task.isCancelled {
                if let frame = try client.pumpOnce(timeout: 0.5) {
                    let img = Self.makeImage(frame)
                    await MainActor.run { self.image = img }
                    try client.requestUpdate(incremental: true)
                }
            }
        } catch {
            await MainActor.run {
                self.phase = .offline(error.localizedDescription)
                self.statusLine = "offline: \(error.localizedDescription)"
            }
        }
    }

    // ---- input (called from the AppKit layer, main thread) ---------------

    /// `point` is in Amiga screen pixels; `mask` bit0=left, bit1=middle, bit2=right.
    func pointer(at point: CGPoint, mask: UInt8) {
        guard let client else { return }
        cursor = point
        let x = Int(point.x), y = Int(point.y)
        Task.detached { try? client.sendPointer(x: x, y: y, buttonMask: mask) }
    }

    func key(_ keysym: UInt32, down: Bool) {
        guard let client else { return }
        Task.detached { try? client.sendKey(keysym: keysym, down: down) }
    }

    private nonisolated static func makeImage(_ f: RFBFrame) -> CGImage? {
        let data = Data(f.rgba)
        guard let provider = CGDataProvider(data: data as CFData) else { return nil }
        return CGImage(width: f.width, height: f.height,
                       bitsPerComponent: 8, bitsPerPixel: 32,
                       bytesPerRow: f.width * 4,
                       space: CGColorSpaceCreateDeviceRGB(),
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                       provider: provider, decode: nil,
                       shouldInterpolate: false, intent: .defaultIntent)
    }

    // ---- drag-drop upload (Mac → Amiga) ----------------------------------

    /// Files dropped on the screen at Amiga pixel `p`: resolve the Workbench
    /// drawer under that point (via the UI-tree) and upload each there, falling
    /// back to RAM: when no drawer is found. The resolved destination is shown.
    func upload(urls: [URL], atAmigaPoint p: CGPoint) {
        Task {
            let dest = await Self.resolveDrawer(at: p, client: machine.client)
            var done: [String] = []
            var failedName: String?
            for (i, url) in urls.enumerated() {
                guard let data = try? Data(contentsOf: url) else { continue }
                let name = url.lastPathComponent
                let path = dest.hasSuffix(":") || dest.hasSuffix("/") ? dest + name : "\(dest)/\(name)"
                let tag = urls.count > 1 ? "[\(i + 1)/\(urls.count)] " : ""
                let showPct = data.count > AmigaClient.maxFrame       // only big files chunk
                toast = "↑ \(tag)\(name) → \(dest)…"
                do {
                    try await machine.client.sendFile(path, data) { frac in
                        Task { @MainActor in
                            self.toast = showPct ? "↑ \(tag)\(name) → \(dest)  \(Int(frac * 100))%"
                                                 : "↑ \(tag)\(name) → \(dest)…"
                        }
                    }
                    done.append(name)
                } catch { failedName = name }
            }
            if let failedName {
                showToast("✗ upload failed: \(failedName)")
            } else if !done.isEmpty {
                showToast("↑ \(done.joined(separator: ", ")) → \(dest)")
            }
        }
    }

    func showToast(_ s: String) {
        toast = s
        let mine = s
        Task { try? await Task.sleep(nanoseconds: 3_500_000_000); if self.toast == mine { self.toast = nil } }
    }

    /// The Workbench drawer under an Amiga point, or "RAM:" as a safe fallback.
    static func resolveDrawer(at p: CGPoint, client: AmigaClient) async -> String {
        guard let text = try? await client.uiTree() else { return "RAM:" }
        return drawer(in: FleetUITree.parse(text).windows, at: p)
    }

    /// Among windows under the point, the smallest is the most specific (avoids
    /// picking the full-screen Workbench backdrop over a drawer).
    static func drawer(in windows: [FleetUIWindow], at p: CGPoint) -> String {
        let hit = windows
            .filter { p.x >= CGFloat($0.x) && p.x < CGFloat($0.x + $0.w) &&
                      p.y >= CGFloat($0.y) && p.y < CGFloat($0.y + $0.h) }
            .min { $0.w * $0.h < $1.w * $1.h }
        if let win = hit, let path = drawerPath(fromTitle: win.title) { return path }
        return "RAM:"
    }

    // ---- live drop target (while a file is dragged over the screen) -------

    private var hoverWindows: [FleetUIWindow] = []

    /// Fetch the window layout once when a drag enters, so hover updates are local.
    func dropHoverBegin() {
        dropTarget = "…"
        Task {
            if let text = try? await machine.client.uiTree() {
                hoverWindows = FleetUITree.parse(text).windows
            }
        }
    }

    func dropHoverMove(at p: CGPoint) {
        dropTarget = "→ " + Self.drawer(in: hoverWindows, at: p)
    }

    func dropHoverEnd() { dropTarget = nil; hoverWindows = [] }

    /// Best-effort AmigaDOS drawer from a Workbench window title. Sub-drawer
    /// windows are titled with their full path ("Work:Games"); disk-root windows
    /// show "Volume  <stats>". Anything else (app windows) → nil → RAM: fallback.
    static func drawerPath(fromTitle title: String) -> String? {
        let comps = title.trimmingCharacters(in: .whitespaces).components(separatedBy: "  ")
        let name = comps[0].trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return nil }
        if name.contains(":") { return name }          // sub-drawer full path
        if comps.count > 1 { return name + ":" }        // disk root "Vol  stats"
        return nil                                       // not a drawer
    }
}

struct RFBView: View {
    @EnvironmentObject var fleet: Fleet
    let machine: Machine
    @StateObject private var session: RFBSession
    @State private var showFiles = false
    @State private var scaleMode: ScaleMode = .fit

    enum ScaleMode { case fit, actual }

    init(machine: Machine) {
        self.machine = machine
        _session = StateObject(wrappedValue: RFBSession(machine: machine))
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("\(machine.name) — VNC").font(WB.topaz(12)).bold().foregroundColor(.white)
                Text(session.statusLine).font(WB.topaz(10)).foregroundColor(.white.opacity(0.85))
                Spacer()
                Picker("", selection: $scaleMode) {
                    Text("Fit").tag(ScaleMode.fit)
                    Text("1×").tag(ScaleMode.actual)
                }
                .pickerStyle(.segmented).labelsHidden().frame(width: 84)
                Button("⛶") { NSApplication.shared.keyWindow?.toggleFullScreen(nil) }
                    .buttonStyle(WBButtonStyle()).help("Full screen")
                Toggle("Fast", isOn: Binding(get: { session.fast }, set: { session.setFast($0) }))
                    .toggleStyle(.checkbox).font(WB.topaz(11)).foregroundColor(.white)
                    .help("Low-bandwidth 256-colour mode (BGR233) — 4× less data, ideal on real 68k")
                Toggle("Files", isOn: $showFiles)
                    .toggleStyle(.checkbox).font(WB.topaz(11)).foregroundColor(.white)
                    .help("Browse the Amiga's drives and drag files to the Mac")
                Circle().fill(session.phase == .streaming ? Color.green : Color.red)
                    .frame(width: 9, height: 9)
            }
            .padding(8)
            .background(WB.blue)

            HStack(spacing: 0) {
                GeometryReader { geo in
                    screenArea(geo)
                        .overlay(alignment: .bottom) { toastOverlay }
                        .overlay(alignment: .top) { dropOverlay }
                }
                if showFiles {
                    Divider()
                    FilesPanel(client: machine.client)
                }
            }
        }
        .background(WB.gray)
        .navigationTitle("\(machine.name) — VNC")
        .onAppear { session.start() }
        .onDisappear { session.stop() }
    }

    @ViewBuilder private func screenArea(_ geo: GeometryProxy) -> some View {
        if scaleMode == .actual, session.phase == .streaming, session.image != nil {
            ScrollView([.horizontal, .vertical]) {
                screenContent(displayed: session.screenSize)
            }
            .frame(width: geo.size.width, height: geo.size.height)
        } else {
            screenContent(displayed: fittedRect(in: geo.size))
                .frame(width: geo.size.width, height: geo.size.height)
        }
    }

    /// A small Amiga-style arrow at the last pointer position (AmiVNC hides the
    /// real sprite, so without this you can't tell where the cursor is).
    @ViewBuilder private func cursorOverlay(displayed: CGSize) -> some View {
        if let c = session.cursor, session.screenSize.width > 0 {
            let x = c.x / session.screenSize.width * displayed.width
            let y = c.y / session.screenSize.height * displayed.height
            Path { p in
                p.move(to: .zero)
                p.addLine(to: CGPoint(x: 0, y: 15))
                p.addLine(to: CGPoint(x: 4, y: 11))
                p.addLine(to: CGPoint(x: 7, y: 17))
                p.addLine(to: CGPoint(x: 9, y: 16))
                p.addLine(to: CGPoint(x: 6, y: 10))
                p.addLine(to: CGPoint(x: 11, y: 10))
                p.closeSubpath()
            }
            .fill(Color.white)
            .overlay(
                Path { p in
                    p.move(to: .zero); p.addLine(to: CGPoint(x: 0, y: 15))
                    p.addLine(to: CGPoint(x: 4, y: 11)); p.addLine(to: CGPoint(x: 7, y: 17))
                    p.addLine(to: CGPoint(x: 9, y: 16)); p.addLine(to: CGPoint(x: 6, y: 10))
                    p.addLine(to: CGPoint(x: 11, y: 10)); p.closeSubpath()
                }.stroke(Color.black, lineWidth: 0.7)
            )
            .frame(width: 11, height: 17)
            .offset(x: x, y: y)
            .allowsHitTesting(false)
        }
    }

    @ViewBuilder private func screenContent(displayed: CGSize) -> some View {
        ZStack {
            WB.darkEdge.opacity(0.6)
            switch session.phase {
            case .streaming where session.image != nil:
                Image(decorative: session.image!, scale: 1)
                    .resizable().interpolation(.none)
                    .frame(width: displayed.width, height: displayed.height)
                    .overlay(alignment: .topLeading) { cursorOverlay(displayed: displayed) }
                RFBInputView(session: session, displayed: displayed, screenSize: session.screenSize)
                    .frame(width: displayed.width, height: displayed.height)
            case .needsInstall:
                installPrompt
            case .offline(let why):
                offlinePrompt(why)
            default:
                Text(session.statusLine).font(WB.topaz(12)).foregroundColor(.white)
            }
        }
    }

    @ViewBuilder private var toastOverlay: some View {
        if let toast = session.toast {
            Text(toast)
                .font(WB.topaz(11)).foregroundColor(.white)
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(WB.blue.opacity(0.92))
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(WB.darkEdge, lineWidth: 1))
                .cornerRadius(4).padding(.bottom, 12)
                .transition(.opacity)
        }
    }

    @ViewBuilder private var dropOverlay: some View {
        if let target = session.dropTarget {
            Text("Drop \(target)")
                .font(WB.topaz(12)).bold().foregroundColor(.white)
                .padding(.horizontal, 14).padding(.vertical, 7)
                .background(WB.amber.opacity(0.95))
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(WB.darkEdge, lineWidth: 1))
                .cornerRadius(4).padding(.top, 12)
        }
    }

    private var installPrompt: some View {
        VStack(spacing: 12) {
            Text("AmiVNC isn’t installed on \(machine.name).")
                .font(WB.topaz(13)).foregroundColor(.white)
            Text("amifleet can install it now with amipkg (downloads from Aminet).")
                .font(WB.topaz(11)).foregroundColor(.white.opacity(0.8))
            Button("Install AmiVNC") { session.installAndStart() }
                .buttonStyle(WBButtonStyle())
        }
        .padding(24)
    }

    private func offlinePrompt(_ why: String) -> some View {
        VStack(spacing: 12) {
            Text("VNC unavailable").font(WB.topaz(13)).bold().foregroundColor(.white)
            Text(why).font(WB.topaz(11)).foregroundColor(.white.opacity(0.85))
                .multilineTextAlignment(.center).frame(maxWidth: 460)
            Button("Retry") { session.retry() }.buttonStyle(WBButtonStyle())
        }
        .padding(24)
    }

    private func fittedRect(in container: CGSize) -> CGSize {
        let s = session.screenSize
        guard s.width > 0, s.height > 0, container.width > 0, container.height > 0 else {
            return CGSize(width: 640, height: 256)
        }
        let scale = min(container.width / s.width, container.height / s.height)
        return CGSize(width: s.width * scale, height: s.height * scale)
    }
}

// ---- AppKit input layer: absolute pointer + keysym keyboard --------------

struct RFBInputView: NSViewRepresentable {
    let session: RFBSession
    let displayed: CGSize
    let screenSize: CGSize

    func makeNSView(context: Context) -> Catcher {
        let v = Catcher()
        v.session = session
        v.registerForDraggedTypes([.fileURL])       // accept files dropped from Finder
        return v
    }

    func updateNSView(_ v: Catcher, context: Context) {
        v.session = session
        v.displayed = displayed
        v.screenSize = screenSize
    }

    final class Catcher: NSView {
        weak var session: RFBSession?
        var displayed: CGSize = .zero
        var screenSize: CGSize = .zero
        private var mask: UInt8 = 0
        private var tracking: NSTrackingArea?

        override var acceptsFirstResponder: Bool { true }
        override var isFlipped: Bool { true }               // top-left origin, like the Amiga

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            if let t = tracking { removeTrackingArea(t) }
            let t = NSTrackingArea(rect: bounds,
                                   options: [.activeInKeyWindow, .mouseMoved, .mouseEnteredAndExited, .inVisibleRect],
                                   owner: self, userInfo: nil)
            addTrackingArea(t); tracking = t
        }

        private func amigaPointFrom(_ p: CGPoint) -> CGPoint {
            guard displayed.width > 0, screenSize.width > 0 else { return .zero }
            let ax = max(0, min(p.x / displayed.width  * screenSize.width,  screenSize.width  - 1))
            let ay = max(0, min(p.y / displayed.height * screenSize.height, screenSize.height - 1))
            return CGPoint(x: ax, y: ay)
        }

        private func amigaPoint(_ event: NSEvent) -> CGPoint {
            amigaPointFrom(convert(event.locationInWindow, from: nil))
        }

        private func send(_ event: NSEvent) {
            session?.pointer(at: amigaPoint(event), mask: mask)
        }

        // ---- drag destination: files dropped from the Mac ----------------

        override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
            session?.dropHoverBegin()
            session?.dropHoverMove(at: amigaPointFrom(convert(sender.draggingLocation, from: nil)))
            return .copy
        }
        override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
            session?.dropHoverMove(at: amigaPointFrom(convert(sender.draggingLocation, from: nil)))
            return .copy
        }
        override func draggingExited(_ sender: NSDraggingInfo?) { session?.dropHoverEnd() }

        override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
            guard let session else { return false }
            session.dropHoverEnd()
            let opts: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
            guard let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self],
                                                                   options: opts) as? [URL],
                  !urls.isEmpty else { return false }
            let pt = amigaPointFrom(convert(sender.draggingLocation, from: nil))
            session.upload(urls: urls, atAmigaPoint: pt)
            return true
        }

        override func mouseMoved(with e: NSEvent)   { send(e) }
        override func mouseDragged(with e: NSEvent) { send(e) }
        override func rightMouseDragged(with e: NSEvent) { send(e) }
        override func otherMouseDragged(with e: NSEvent) { send(e) }

        override func mouseDown(with e: NSEvent)  { mask |= 1; send(e); window?.makeFirstResponder(self) }
        override func mouseUp(with e: NSEvent)    { mask &= ~1; send(e) }
        override func rightMouseDown(with e: NSEvent) { mask |= 4; send(e) }
        override func rightMouseUp(with e: NSEvent)   { mask &= ~4; send(e) }
        override func otherMouseDown(with e: NSEvent) { mask |= 2; send(e) }
        override func otherMouseUp(with e: NSEvent)   { mask &= ~2; send(e) }

        // ---- keyboard: NSEvent → X11 keysym ------------------------------

        override func keyDown(with e: NSEvent) { sendKey(e, down: true) }
        override func keyUp(with e: NSEvent)   { sendKey(e, down: false) }

        override func flagsChanged(with e: NSEvent) {
            // Track modifier transitions and mirror them as keysym down/up.
            func edge(_ flag: NSEvent.ModifierFlags, _ keysym: UInt32, _ store: inout Bool) {
                let on = e.modifierFlags.contains(flag)
                if on != store { session?.key(keysym, down: on); store = on }
            }
            edge(.shift,   0xFFE1, &shiftDown)
            edge(.control, 0xFFE3, &ctrlDown)
            edge(.option,  0xFFE9, &altDown)      // Alt → Amiga Amiga-key territory
            edge(.command, 0xFFE7, &cmdDown)      // Meta → Amiga (right-Amiga)
        }
        private var shiftDown = false, ctrlDown = false, altDown = false, cmdDown = false

        private func sendKey(_ e: NSEvent, down: Bool) {
            guard let session else { return }
            if let ks = Self.specialKeysyms[e.keyCode] {
                session.key(ks, down: down)
                return
            }
            if let chars = e.charactersIgnoringModifiers, let scalar = chars.unicodeScalars.first,
               scalar.value >= 0x20 {
                session.key(scalar.value, down: down)   // Latin-1/ASCII keysym == code point
            }
        }

        /// macOS virtual keycode → X11 keysym for non-printable keys.
        static let specialKeysyms: [UInt16: UInt32] = [
            36: 0xFF0D,  // Return
            76: 0xFF8D,  // Enter (keypad)
            48: 0xFF09,  // Tab
            51: 0xFF08,  // BackSpace
            53: 0xFF1B,  // Escape
            117: 0xFFFF, // Delete (forward)
            123: 0xFF51, 124: 0xFF53, 125: 0xFF54, 126: 0xFF52,  // ← → ↓ ↑
            115: 0xFF50, // Home
            119: 0xFF57, // End
            116: 0xFF55, // Page Up
            121: 0xFF56, // Page Down
            122: 0xFFBE, 120: 0xFFBF, 99: 0xFFC0, 118: 0xFFC1, 96: 0xFFC2,   // F1–F5
            97: 0xFFC3, 98: 0xFFC4, 100: 0xFFC5, 101: 0xFFC6, 109: 0xFFC7,   // F6–F10
        ]
    }
}
