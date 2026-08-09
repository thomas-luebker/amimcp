// Models.swift — the fleet: which Amigas exist, and what each one said last.

import Foundation
import SwiftUI
import Darwin
import AmigaKit

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
        // A fresh install starts EMPTY — the user connects via Scan LAN or Add…
        // (no personal fleet baked into the shipped app). An existing config is
        // restored as-is.
        if let data = UserDefaults.standard.data(forKey: Self.defaultsKey),
           let saved = try? JSONDecoder().decode([Machine].self, from: data) {
            machines = saved
        } else {
            machines = []
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

    func update(_ m: Machine) {
        guard let i = machines.firstIndex(where: { $0.id == m.id }) else { return }
        machines[i] = m; save(); refreshNow()
    }

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

    /// Sweep this Mac's own /24(s) for agents on port 7846, tagging each with
    /// `token`. Detects both open agents and token-protected ones (a protected
    /// agent answers a token-less PING with AUTH_REQUIRED, which is still a
    /// positive ID — we just apply the user's token to the entry we add).
    func scan(token: String) {
        guard !scanning else { return }
        let hosts = Self.localSubnets().flatMap { base in (1...254).map { "\(base).\($0)" } }
        guard !hosts.isEmpty else { return }
        scanning = true
        Task {
            var found: [Machine] = []
            let maxInFlight = 48        // bound concurrent sockets on blocking I/O
            await withTaskGroup(of: (String, Bool).self) { group in
                var it = hosts.makeIterator()
                func launch() -> Bool {
                    guard let host = it.next() else { return false }
                    group.addTask {
                        // Probe token-less: a banner or an AUTH_REQUIRED both
                        // mean "an agent lives here".
                        let probe = AmigaClient(host: host, port: 7846, token: "")
                        do { _ = try await probe.ping(timeout: 1.2); return (host, true) }
                        catch AmigaWireError.authRequired { return (host, true) }
                        catch { return (host, false) }
                    }
                    return true
                }
                for _ in 0..<maxInFlight where launch() {}
                for await (host, ok) in group {
                    if ok { found.append(Machine(name: "amiga @ \(host)", host: host, token: token)) }
                    _ = launch()
                }
            }
            let known = Set(machines.map(\.host))
            for m in found.sorted(by: { $0.host < $1.host }) where !known.contains(m.host) {
                machines.append(m)
            }
            save()
            scanning = false
            refreshNow()
        }
    }

    /// The /24 prefixes of every up, non-loopback IPv4 interface on this Mac.
    static func localSubnets() -> [String] {
        var out: Set<String> = []
        var ifap: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifap) == 0 else { return [] }
        defer { freeifaddrs(ifap) }
        var p = ifap
        while let cur = p {
            let flags = Int32(cur.pointee.ifa_flags)
            if (flags & IFF_UP) != 0, (flags & IFF_LOOPBACK) == 0,
               let a = cur.pointee.ifa_addr, a.pointee.sa_family == UInt8(AF_INET) {
                var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                if getnameinfo(a, socklen_t(a.pointee.sa_len), &host, socklen_t(host.count),
                               nil, 0, NI_NUMERICHOST) == 0 {
                    let parts = String(cString: host).split(separator: ".")
                    if parts.count == 4 { out.insert(parts.prefix(3).joined(separator: ".")) }
                }
            }
            p = cur.pointee.ifa_next
        }
        return Array(out)
    }
}
