// InspectorView.swift — the "amiinspect" panel: the frontmost screen's window/
// gadget tree beside the live view. Click a gadget row to drive the Amiga by
// object (uiClick). The analog of AmiPilot's inspector, on the Mac side.

import SwiftUI
import AmigaKit

struct InspectorPanel: View {
    let client: AmigaClient
    /// Called after an action so the live screen pulls a fresh frame.
    var onAction: () -> Void = {}

    @State private var tree = FleetUITree(screen: "", windows: [])
    @State private var status = "Loading object tree…"
    @State private var busy = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Text("Inspector").font(WB.topaz(12)).bold().foregroundColor(.white)
                if !tree.screen.isEmpty {
                    Text(tree.screen).font(WB.topaz(9)).foregroundColor(.white.opacity(0.7))
                        .lineLimit(1)
                }
                Spacer()
                Button(busy ? "…" : "Refresh") { load() }
                    .buttonStyle(WBButtonStyle()).disabled(busy)
            }
            .padding(6)
            .background(WB.blue)

            if tree.windows.isEmpty {
                Text(status).font(WB.topaz(11)).foregroundColor(WB.darkEdge)
                    .multilineTextAlignment(.center).padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(tree.windows) { win in
                            VStack(alignment: .leading, spacing: 1) {
                                HStack(spacing: 4) {
                                    if win.active {
                                        Circle().fill(.green).frame(width: 7, height: 7)
                                    }
                                    Text(win.displayTitle).font(WB.topaz(11)).bold()
                                        .foregroundColor(.black).lineLimit(1)
                                }
                                .padding(.horizontal, 6).padding(.vertical, 3)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(WB.darkGray.opacity(0.25))

                                ForEach(win.gadgets) { g in
                                    GadgetRow(gadget: g) { act(win, g) }
                                }
                            }
                            .bevel()
                        }
                    }
                    .padding(8)
                }
            }
        }
        .frame(width: 300)
        .background(WB.gray)
        .onAppear { load() }
    }

    private func load() {
        busy = true
        Task {
            defer { busy = false }
            do {
                let t = try await client.uiTree()
                tree = FleetUITree.parse(t)
                if tree.windows.isEmpty { status = "No standard-GUI windows on the frontmost screen." }
            } catch {
                status = error.localizedDescription
            }
        }
    }

    private func act(_ win: FleetUIWindow, _ g: FleetUIGadget) {
        busy = true
        Task {
            defer { busy = false }
            _ = try? await client.uiClick(gadget: g.selector, window: win.title)
            onAction()
            try? await Task.sleep(nanoseconds: 400_000_000)
            load()
        }
    }
}

/// One clickable gadget row: role tag, label/value, and a hint of its bounds.
struct GadgetRow: View {
    let gadget: FleetUIGadget
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(gadget.kind).font(WB.topaz(9))
                    .foregroundColor(WB.blue).frame(width: 62, alignment: .leading)
                Text(gadget.display).font(WB.topaz(11)).foregroundColor(.black)
                    .lineLimit(1)
                Spacer(minLength: 4)
                if gadget.state != "-" {
                    Text(gadget.state).font(WB.topaz(9)).foregroundColor(WB.darkEdge)
                }
            }
            .padding(.horizontal, 6).padding(.vertical, 2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("Click \(gadget.display) on the Amiga (\(gadget.x),\(gadget.y) \(gadget.w)×\(gadget.h))")
    }
}
