// DetailView.swift — one machine up close: the INFO report, and a shell that
// EXECs AmigaDOS commands and shows rc + captured output.

import SwiftUI

struct DetailView: View {
    @EnvironmentObject var fleet: Fleet
    let machine: Machine

    @State private var command = ""
    @State private var transcript: [ShellEntry] = []
    @State private var running = false

    struct ShellEntry: Identifiable {
        let id = UUID()
        let command: String
        let rc: Int32
        let output: String
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            HSplitView {
                report
                    .frame(minWidth: 230, maxWidth: 320)
                shell
                    .frame(minWidth: 300, maxWidth: .infinity)
            }
        }
        .background(WB.gray)
        .navigationTitle("\(machine.name) — \(machine.host)")
    }

    private var status: MachineStatus { fleet.status[machine.id] ?? MachineStatus() }

    private var header: some View {
        HStack(spacing: 8) {
            Circle().fill(status.online ? .green : .red).frame(width: 9, height: 9)
            Text("\(machine.name)  ·  \(status.agent)").font(WB.topaz(12)).bold()
                .foregroundColor(.white)
            Spacer()
            Button("Refresh") { fleet.refreshNow() }.buttonStyle(WBButtonStyle())
        }
        .padding(8)
        .background(status.online ? WB.blue : WB.darkGray)
    }

    private var report: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                Text("Report").font(WB.topaz(12)).bold().padding(.bottom, 2)
                ForEach(status.info.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                    VStack(alignment: .leading, spacing: 0) {
                        Text(key).font(WB.topaz(10)).foregroundColor(WB.darkEdge)
                        Text(value).font(WB.topaz(11)).foregroundColor(.black)
                            .textSelection(.enabled)
                    }
                    .padding(.bottom, 3)
                }
                if let seen = status.lastSeen {
                    Text("last seen \(seen.formatted(date: .omitted, time: .standard))")
                        .font(WB.topaz(10)).foregroundColor(WB.darkEdge).padding(.top, 4)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
        }
        .background(Color.white.opacity(0.4))
        .bevel(sunken: true)
        .padding(8)
    }

    private var shell: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        if transcript.isEmpty {
                            Text("AmigaDOS shell — commands run on \(machine.name) via EXEC.")
                                .font(WB.topaz(11)).foregroundColor(WB.darkEdge)
                        }
                        ForEach(transcript) { e in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("> \(e.command)").font(WB.topaz(11)).bold()
                                    .foregroundColor(WB.blue)
                                Text(e.output.isEmpty ? "(no output)" : e.output)
                                    .font(WB.topaz(11)).foregroundColor(.black)
                                    .textSelection(.enabled)
                                if e.rc != 0 {
                                    Text("rc \(e.rc)").font(WB.topaz(10)).foregroundColor(.red)
                                }
                            }
                            .id(e.id)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                }
                .onChange(of: transcript.count) { _ in
                    if let last = transcript.last { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
            .background(Color.white.opacity(0.55))
            .bevel(sunken: true)

            HStack(spacing: 8) {
                Text(running ? "…" : ">").font(WB.topaz(12)).bold()
                TextField("Dir SYS: — press Return", text: $command)
                    .textFieldStyle(.plain)
                    .font(WB.topaz(12))
                    .onSubmit(run)
                    .disabled(running)
            }
            .padding(8)
            .background(Color.white.opacity(0.7))
            .bevel(sunken: true)
        }
        .padding(8)
    }

    private func run() {
        let cmd = command.trimmingCharacters(in: .whitespaces)
        guard !cmd.isEmpty, !running else { return }
        command = ""
        running = true
        Task {
            defer { running = false }
            do {
                let (rc, out) = try await machine.client.exec(cmd)
                transcript.append(ShellEntry(command: cmd, rc: rc,
                    output: out.trimmingCharacters(in: .whitespacesAndNewlines)))
            } catch {
                transcript.append(ShellEntry(command: cmd, rc: -1,
                    output: error.localizedDescription))
            }
        }
    }
}
