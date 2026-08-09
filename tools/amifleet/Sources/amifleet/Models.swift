// Models.swift — the fleet: which Amigas exist, and what each one said last.

import Foundation
import SwiftUI

struct Machine: Identifiable, Codable, Hashable {
    var id = UUID()
    var name: String
    var host: String
    var port: UInt16 = 7846
    var token: String = ""

    var client: AmigaClient { AmigaClient(host: host, port: port, token: token) }
}

struct MachineStatus {
    var online = false
    var latencyMs: Int?
    var info: [String: String] = [:]
    var lastError: String?
    var lastSeen: Date?

    var agent: String { info["agent"] ?? "—" }
    var cpu: String { info["cpu"] ?? "—" }
    var kickstart: String { info["kickstart"] ?? "—" }

    func ramLine() -> String {
        func mb(_ key: String) -> String {
            guard let s = info[key], let v = Int(s) else { return "—" }
            return v >= 10 * 1024 * 1024 ? "\(v / (1024 * 1024)) MB" : "\(v / 1024) KB"
        }
        return "chip \(mb("chipram_free"))  fast \(mb("fastram_free"))"
    }
}

@MainActor
final class Fleet: ObservableObject {
    @Published var machines: [Machine] = []
    @Published var status: [UUID: MachineStatus] = [:]
    @Published var scanning = false

    private var pollTask: Task<Void, Never>?
    private static let defaultsKey = "amifleet.machines"

    init() {
        load()
        startPolling()
    }

    // ---- persistence -----------------------------------------------------

    private func load() {
        if let data = UserDefaults.standard.data(forKey: Self.defaultsKey),
           let saved = try? JSONDecoder().decode([Machine].self, from: data), !saved.isEmpty {
            machines = saved
        } else {
            machines = [
                Machine(name: "A4000", host: "192.168.178.21", token: "a4000"),
                Machine(name: "PiStorm", host: "192.168.178.178", token: "a4000"),
                Machine(name: "FS-UAE", host: "127.0.0.1", token: "a4000"),
            ]
            save()
        }
    }

    func save() {
        if let data = try? JSONEncoder().encode(machines) {
            UserDefaults.standard.set(data, forKey: Self.defaultsKey)
        }
    }

    func add(_ m: Machine) { machines.append(m); save(); refreshNow() }
    func remove(_ m: Machine) { machines.removeAll { $0.id == m.id }; status[m.id] = nil; save() }
    func machine(_ id: UUID?) -> Machine? { machines.first { $0.id == id } }

    // ---- polling ---------------------------------------------------------

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollAll()
                try? await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }

    func refreshNow() { Task { await pollAll() } }

    private func pollAll() async {
        await withTaskGroup(of: (UUID, MachineStatus).self) { group in
            for m in machines {
                group.addTask {
                    var st = MachineStatus()
                    let t0 = Date()
                    do {
                        st.info = try await m.client.info()
                        st.online = true
                        st.latencyMs = Int(Date().timeIntervalSince(t0) * 1000)
                        st.lastSeen = Date()
                    } catch {
                        st.online = false
                        st.lastError = error.localizedDescription
                    }
                    return (m.id, st)
                }
            }
            for await (id, st) in group {
                var merged = st
                if !st.online, let old = status[id] {
                    merged.info = old.info          // keep the last good report
                    merged.lastSeen = old.lastSeen
                }
                status[id] = merged
            }
        }
    }

    // ---- discovery -------------------------------------------------------

    /// Sweep the /24 of the first configured LAN machine for port 7846.
    func scan() {
        guard !scanning else { return }
        let base = machines.map(\.host).first { $0.contains(".") && $0 != "127.0.0.1" }
            .map { $0.split(separator: ".").dropLast().joined(separator: ".") } ?? "192.168.178"
        let token = machines.first?.token ?? ""
        scanning = true
        Task {
            var found: [Machine] = []
            await withTaskGroup(of: Machine?.self) { group in
                for i in 1...254 {
                    group.addTask {
                        let host = "\(base).\(i)"
                        let probe = AmigaClient(host: host, port: 7846, token: token)
                        guard let banner = try? await probe.ping() else { return nil }
                        return Machine(name: banner.contains("amiagent") ? host : "\(host)?",
                                       host: host, token: token)
                    }
                }
                for await hit in group { if let hit { found.append(hit) } }
            }
            let known = Set(machines.map(\.host))
            for m in found where !known.contains(m.host) { machines.append(m) }
            save()
            scanning = false
            refreshNow()
        }
    }
}
