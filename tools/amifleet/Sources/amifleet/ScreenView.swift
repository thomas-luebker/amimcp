// ScreenView.swift — the "VNC" window: a live view of the Amiga's frontmost
// screen with mouse and keyboard forwarded through CMD_INPUT.
//
// Refresh strategy is straight from the protocol's design notes: poll the
// cheap HASH twice a second (no pixels move), and pull a full SHOT only when
// the checksum changes. A 640x256 palette screen is a ~160 KB fetch; a 1080p
// RTG screen is ~6 MB and takes seconds on a real 68060 — the header shows
// the fetch time so slowness is visible instead of mysterious.

import SwiftUI
import CoreGraphics
import AmigaKit

@MainActor
final class ScreenSession: ObservableObject {
    @Published var image: CGImage?
    @Published var screenSize = CGSize(width: 640, height: 256)
    @Published var statusLine = "connecting…"
    @Published var paused = false

    private let client: AmigaClient
    private var loop: Task<Void, Never>?
    private var lastHash: UInt32?

    init(client: AmigaClient) {
        self.client = client
    }

    func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            await self?.run()
        }
    }

    func stop() { loop?.cancel(); loop = nil }

    /// Force the next heartbeat to pull a fresh frame (after an inspector action).
    func forceRefresh() { lastHash = nil }

    private func run() async {
        await fetchFrame()
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard !paused else { continue }
            do {
                let h = try await client.regionHash()
                if h != lastHash {
                    lastHash = h
                    await fetchFrame()
                }
            } catch {
                statusLine = "offline: \(error.localizedDescription)"
            }
        }
    }

    private func fetchFrame() async {
        do {
            let t0 = Date()
            let shot = try await client.screenshot()
            let ms = Int(Date().timeIntervalSince(t0) * 1000)
            screenSize = CGSize(width: shot.width, height: shot.height)
            image = Self.makeImage(shot)
            statusLine = "\(shot.width)×\(shot.height) — frame in \(ms) ms"
        } catch {
            statusLine = "capture failed: \(error.localizedDescription)"
        }
    }

    private static func makeImage(_ shot: AmigaScreenshot) -> CGImage? {
        let data = Data(shot.rgba)
        guard let provider = CGDataProvider(data: data as CFData) else { return nil }
        return CGImage(width: shot.width, height: shot.height,
                       bitsPerComponent: 8, bitsPerPixel: 32,
                       bytesPerRow: shot.width * 4,
                       space: CGColorSpaceCreateDeviceRGB(),
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                       provider: provider, decode: nil,
                       shouldInterpolate: false, intent: .defaultIntent)
    }

    // ---- input forwarding ------------------------------------------------

    /// `point` is in *Amiga screen pixels* (the input view does the mapping).
    func click(at point: CGPoint, button: UInt8, count: Int) {
        let x = max(0, min(Int(point.x), Int(screenSize.width) - 1))
        let y = max(0, min(Int(point.y), Int(screenSize.height) - 1))
        Task {
            try? await client.click(x: x, y: y, button: button, count: UInt8(min(count, 3)))
            try? await Task.sleep(nanoseconds: 150_000_000)
            lastHash = nil                       // force a refresh soon after
        }
    }

    func type(_ text: String) {
        Task { try? await client.type(text); lastHash = nil }
    }

    func pressKey(named name: String) {
        guard let code = amigaRawkeys[name] else { return }
        Task { try? await client.pressKey(rawcode: code); lastHash = nil }
    }
}

struct ScreenView: View {
    @EnvironmentObject var fleet: Fleet
    let machine: Machine
    @StateObject private var session: ScreenSession
    @State private var showInspector = false

    init(machine: Machine) {
        self.machine = machine
        _session = StateObject(wrappedValue: ScreenSession(client: machine.client))
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("\(machine.name) — live").font(WB.topaz(12)).bold().foregroundColor(.white)
                Text(session.statusLine).font(WB.topaz(10)).foregroundColor(.white.opacity(0.8))
                Spacer()
                Toggle("Inspect", isOn: $showInspector)
                    .toggleStyle(.checkbox).font(WB.topaz(11)).foregroundColor(.white)
                Toggle("Pause", isOn: $session.paused)
                    .toggleStyle(.checkbox).font(WB.topaz(11)).foregroundColor(.white)
            }
            .padding(8)
            .background(WB.blue)

            HStack(spacing: 0) {
                GeometryReader { geo in
                    let fitted = fittedRect(in: geo.size)
                    ZStack {
                        WB.darkEdge.opacity(0.6)
                        if let cg = session.image {
                            Image(decorative: cg, scale: 1)
                                .resizable()
                                .interpolation(.none)      // chunky pixels, as nature intended
                                .frame(width: fitted.width, height: fitted.height)
                        } else {
                            Text("waiting for the first frame…")
                                .font(WB.topaz(12)).foregroundColor(.white)
                        }
                        RemoteInputView(session: session,
                                        displayed: fitted,
                                        screenSize: session.screenSize)
                            .frame(width: fitted.width, height: fitted.height)
                    }
                    .frame(width: geo.size.width, height: geo.size.height)
                }
                if showInspector {
                    Divider()
                    InspectorPanel(client: machine.client) { session.forceRefresh() }
                }
            }
        }
        .background(WB.gray)
        .navigationTitle("\(machine.name) — screen")
        .onAppear { session.start() }
        .onDisappear { session.stop() }
    }

    private func fittedRect(in container: CGSize) -> CGSize {
        let s = session.screenSize
        guard s.width > 0, s.height > 0, container.width > 0, container.height > 0 else {
            return CGSize(width: 320, height: 200)
        }
        let scale = min(container.width / s.width, container.height / s.height)
        return CGSize(width: s.width * scale, height: s.height * scale)
    }
}

// ---- AppKit layer: raw mouse + keyboard capture --------------------------

struct RemoteInputView: NSViewRepresentable {
    let session: ScreenSession
    let displayed: CGSize
    let screenSize: CGSize

    func makeNSView(context: Context) -> InputCatcher {
        let v = InputCatcher()
        v.session = session
        return v
    }

    func updateNSView(_ v: InputCatcher, context: Context) {
        v.session = session
        v.displayed = displayed
        v.screenSize = screenSize
    }

    final class InputCatcher: NSView {
        weak var session: ScreenSession?
        var displayed: CGSize = .zero
        var screenSize: CGSize = .zero

        override var acceptsFirstResponder: Bool { true }
        override var isFlipped: Bool { true }      // top-left origin, like the Amiga

        override func mouseDown(with event: NSEvent) {
            forward(event, button: 0)
            window?.makeFirstResponder(self)
        }
        override func rightMouseDown(with event: NSEvent) { forward(event, button: 1) }
        override func otherMouseDown(with event: NSEvent) { forward(event, button: 2) }

        private func forward(_ event: NSEvent, button: UInt8) {
            guard displayed.width > 0, screenSize.width > 0 else { return }
            let p = convert(event.locationInWindow, from: nil)
            let ax = p.x / displayed.width * screenSize.width
            let ay = p.y / displayed.height * screenSize.height
            session?.click(at: CGPoint(x: ax, y: ay), button: button,
                           count: max(1, event.clickCount))
        }

        override func keyDown(with event: NSEvent) {
            guard let session else { return }
            if let name = Self.specialKeys[event.keyCode] {
                session.pressKey(named: name)
                return
            }
            if let chars = event.characters, !chars.isEmpty,
               !event.modifierFlags.contains(.command) {
                session.type(chars)
            }
        }
        override func keyUp(with event: NSEvent) {}     // handled as press+release pairs

        /// macOS virtual keycode → Amiga rawkey name (see amigaRawkeys).
        static let specialKeys: [UInt16: String] = [
            36: "return", 48: "tab", 51: "backspace", 53: "esc",
            76: "enter", 114: "help", 117: "delete",
            123: "left", 124: "right", 125: "down", 126: "up",
            122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5",
            97: "f6", 98: "f7", 100: "f8", 101: "f9", 109: "f10",
        ]
    }
}
