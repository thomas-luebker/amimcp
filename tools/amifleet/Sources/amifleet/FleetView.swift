// FleetView.swift — the board: one beveled tile per Amiga, Apple-Remote-
// Desktop style, dressed like a Workbench 3.x window.

import SwiftUI

struct FleetView: View {
    @EnvironmentObject var fleet: Fleet
    @Environment(\.openWindow) private var openWindow
    @State private var adding = false

    private let columns = [GridItem(.adaptive(minimum: 300), spacing: 12)]

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("amifleet").font(WB.topaz(13)).foregroundColor(.white).bold()
                Text("\(fleet.machines.count) machine\(fleet.machines.count == 1 ? "" : "s"), \(onlineCount) online")
                    .font(WB.topaz(11)).foregroundColor(.white.opacity(0.8))
                Spacer()
                Button(fleet.scanning ? "Scanning…" : "Scan LAN") { fleet.scan() }
                    .buttonStyle(WBButtonStyle()).disabled(fleet.scanning)
                Button("Add…") { adding = true }.buttonStyle(WBButtonStyle())
                Button("Refresh") { fleet.refreshNow() }.buttonStyle(WBButtonStyle())
            }
            .padding(8)
            .background(WB.blue)

            ScrollView {
                LazyVGrid(columns: columns, spacing: 12) {
                    ForEach(fleet.machines) { m in
                        MachineTile(machine: m, status: fleet.status[m.id] ?? MachineStatus())
                            .contextMenu {
                                Button("Open Screen") { openWindow(id: "screen", value: m.id) }
                                Button("Report & Shell") { openWindow(id: "detail", value: m.id) }
                                Divider()
                                Button("Remove", role: .destructive) { fleet.remove(m) }
                            }
                    }
                }
                .padding(12)
            }
        }
        .background(WB.gray)
        .sheet(isPresented: $adding) { AddMachineSheet() }
    }

    private var onlineCount: Int {
        fleet.machines.filter { fleet.status[$0.id]?.online == true }.count
    }
}

struct MachineTile: View {
    @EnvironmentObject var fleet: Fleet
    @Environment(\.openWindow) private var openWindow
    let machine: Machine
    let status: MachineStatus

    var body: some View {
        VStack(spacing: 0) {
            // title strip, like a WB window drag bar
            HStack(spacing: 6) {
                Circle().fill(status.online ? .green : .red)
                    .frame(width: 9, height: 9)
                    .overlay(Circle().stroke(WB.darkEdge, lineWidth: 0.5))
                Text(machine.name).font(WB.topaz(12)).bold().foregroundColor(.white)
                Text(machine.host).font(WB.topaz(10)).foregroundColor(.white.opacity(0.75))
                Spacer()
                if let ms = status.latencyMs {
                    Text("\(ms) ms").font(WB.topaz(10)).foregroundColor(.white.opacity(0.75))
                }
            }
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(status.online ? WB.blue : WB.darkGray)

            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "desktopcomputer")
                    .font(.system(size: 34))
                    .foregroundColor(status.online ? WB.blue : WB.darkGray)
                    .frame(width: 52, height: 52)
                    .background(Color.white.opacity(0.35))
                    .bevel(sunken: true)

                VStack(alignment: .leading, spacing: 3) {
                    row("Agent", status.agent)
                    row("CPU", "\(status.cpu)  KS \(status.kickstart)")
                    row("RAM", status.ramLine())
                    if !status.online, let err = status.lastError {
                        Text(err).font(WB.topaz(10)).foregroundColor(.red)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(8)

            HStack(spacing: 8) {
                Button("Screen") { openWindow(id: "screen", value: machine.id) }
                    .buttonStyle(WBButtonStyle()).disabled(!status.online)
                Button("Report") { openWindow(id: "detail", value: machine.id) }
                    .buttonStyle(WBButtonStyle())
                Spacer()
            }
            .padding(.horizontal, 8).padding(.bottom, 8)
        }
        .background(WB.gray)
        .bevel()
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(spacing: 6) {
            Text(label).font(WB.topaz(11)).foregroundColor(WB.darkEdge)
                .frame(width: 44, alignment: .leading)
            Text(value).font(WB.topaz(11)).foregroundColor(.black)
                .lineLimit(1).truncationMode(.middle)
        }
    }
}

struct AddMachineSheet: View {
    @EnvironmentObject var fleet: Fleet
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var host = ""
    @State private var port = "7846"
    @State private var token = "a4000"

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Add a machine").font(WB.topaz(13)).bold()
            Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 6) {
                GridRow { Text("Name").font(WB.topaz()); TextField("A1200", text: $name) }
                GridRow { Text("Host").font(WB.topaz()); TextField("192.168.178.x", text: $host) }
                GridRow { Text("Port").font(WB.topaz()); TextField("7846", text: $port) }
                GridRow { Text("Token").font(WB.topaz()); TextField("", text: $token) }
            }
            .textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(WBButtonStyle())
                Button("Add") {
                    fleet.add(Machine(name: name.isEmpty ? host : name, host: host,
                                      port: UInt16(port) ?? 7846, token: token))
                    dismiss()
                }
                .buttonStyle(WBButtonStyle())
                .disabled(host.isEmpty)
            }
        }
        .padding(16)
        .frame(width: 380)
        .background(WB.gray)
    }
}
