// App.swift — amifleet entry point. Three window kinds: the fleet board,
// a detail (report + shell) window per machine, and a live screen per machine.

import SwiftUI

@main
struct AmifleetApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var fleet = Fleet()

    var body: some Scene {
        Window("amifleet — Amiga fleet", id: "fleet") {
            FleetView()
                .environmentObject(fleet)
        }
        .defaultSize(width: 720, height: 420)

        WindowGroup("Machine", id: "detail", for: UUID.self) { $id in
            if let m = fleet.machine(id) {
                DetailView(machine: m)
                    .environmentObject(fleet)
            }
        }
        .defaultSize(width: 640, height: 520)

        WindowGroup("Screen", id: "screen", for: UUID.self) { $id in
            if let m = fleet.machine(id) {
                ScreenView(machine: m)
                    .environmentObject(fleet)
            }
        }
        .defaultSize(width: 980, height: 620)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // `swift run` starts us as a bare executable; claim a real UI presence.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

// ---- Workbench 3.x dress code -------------------------------------------

enum WB {
    static let gray = Color(red: 0.66, green: 0.66, blue: 0.66)
    static let darkGray = Color(red: 0.45, green: 0.45, blue: 0.45)
    static let blue = Color(red: 0.0, green: 0.33, blue: 0.67)
    static let lightEdge = Color.white
    static let darkEdge = Color(red: 0.22, green: 0.22, blue: 0.22)
    static let amber = Color(red: 1.0, green: 0.72, blue: 0.2)
    static func topaz(_ size: CGFloat = 12) -> Font {
        .system(size: size, weight: .medium, design: .monospaced)
    }
}

/// The classic embossed border: light on top/left, dark on bottom/right —
/// or the inverse for a "pressed"/sunken look.
struct Bevel: ViewModifier {
    var sunken = false
    func body(content: Content) -> some View {
        content.overlay(
            GeometryReader { geo in
                let w = geo.size.width, h = geo.size.height
                Path { p in
                    p.move(to: .init(x: 0, y: h)); p.addLine(to: .zero)
                    p.addLine(to: .init(x: w, y: 0))
                }
                .stroke(sunken ? WB.darkEdge : WB.lightEdge, lineWidth: 2)
                Path { p in
                    p.move(to: .init(x: w, y: 0)); p.addLine(to: .init(x: w, y: h))
                    p.addLine(to: .init(x: 0, y: h))
                }
                .stroke(sunken ? WB.lightEdge : WB.darkEdge, lineWidth: 2)
            }
        )
    }
}

extension View {
    func bevel(sunken: Bool = false) -> some View { modifier(Bevel(sunken: sunken)) }
}

struct WBButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(WB.topaz())
            .foregroundColor(.black)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(WB.gray)
            .bevel(sunken: configuration.isPressed)
    }
}
