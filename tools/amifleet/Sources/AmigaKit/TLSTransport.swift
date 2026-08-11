// TLSTransport.swift — the encrypted transport for the amimcp wire protocol.
//
// Pure Swift, no dependencies: Network.framework (NWConnection + NWProtocolTLS).
// The agent's cert is self-signed, so we pin it trust-on-first-use — the first
// connection to a host records the cert's SHA-256, and every later connection
// must present the same one (a changed fingerprint is refused, like SSH).
//
// The public protocol above is spoken byte-for-byte inside the TLS tunnel; this
// just replaces "connect + send + recv" with an encrypted channel. NWConnection
// is callback-based, so each call blocks the caller on a semaphore — the wire
// client is synchronous by design (see AmigaWire), and callers are already off
// the main actor.

import Foundation
import Network
import CryptoKit

/// Remembers, per host, whether TLS is worth trying — so a plain-only machine
/// isn't probed on the TLS port before every single request.
enum TLSAvailability {
    private static let lock = NSLock()
    private static var known: [String: Bool] = [:]     // host → TLS works

    static func shouldTry(_ host: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return known[host] ?? true                     // unknown → try once
    }
    static func record(_ host: String, works: Bool) {
        lock.lock(); defer { lock.unlock() }
        known[host] = works
    }
    static func status(_ host: String) -> Bool? {
        lock.lock(); defer { lock.unlock() }
        return known[host]
    }
}

/// Trust-on-first-use fingerprint store, persisted so pins survive relaunches.
enum TOFUStore {
    private static let key = "amikit.tls.pins"
    private static let lock = NSLock()

    static func check(host: String, fingerprint: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        var pins = UserDefaults.standard.dictionary(forKey: key) as? [String: String] ?? [:]
        if let pinned = pins[host] { return pinned == fingerprint }   // must match
        pins[host] = fingerprint                                      // first use → trust
        UserDefaults.standard.set(pins, forKey: key)
        return true
    }
}

enum TLSError: LocalizedError {
    case unavailable(String)
    var errorDescription: String? { if case .unavailable(let s) = self { return s }; return nil }
}

final class TLSTransport {
    private let conn: NWConnection
    private let queue = DispatchQueue(label: "amikit.tls")

    init(host: String, port: UInt16) {
        let tls = NWProtocolTLS.Options()
        sec_protocol_options_set_min_tls_protocol_version(tls.securityProtocolOptions, .TLSv12)
        // Trust-on-first-use pinning: verify the leaf cert's SHA-256 ourselves.
        sec_protocol_options_set_verify_block(tls.securityProtocolOptions, { _, trust, complete in
            let secTrust = sec_trust_copy_ref(trust).takeRetainedValue()
            guard let chain = SecTrustCopyCertificateChain(secTrust) as? [SecCertificate],
                  let leaf = chain.first else { complete(false); return }
            let der = SecCertificateCopyData(leaf) as Data
            let fp = SHA256.hash(data: der).map { String(format: "%02X", $0) }.joined()
            complete(TOFUStore.check(host: host, fingerprint: fp))
        }, queue)

        let params = NWParameters(tls: tls, tcp: NWProtocolTCP.Options())
        conn = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!,
                            using: params)
    }

    /// Connect and finish the TLS handshake, or throw within `timeout`.
    func open(timeout: TimeInterval) throws {
        let sem = DispatchSemaphore(value: 0)
        var failure: Error?
        conn.stateUpdateHandler = { state in
            switch state {
            case .ready: sem.signal()
            case .failed(let e), .waiting(let e): failure = e; sem.signal()
            case .cancelled: failure = failure ?? TLSError.unavailable("cancelled"); sem.signal()
            default: break
            }
        }
        conn.start(queue: queue)
        if sem.wait(timeout: .now() + timeout) == .timedOut {
            conn.cancel(); throw TLSError.unavailable("TLS connect timed out")
        }
        if let failure { conn.cancel(); throw TLSError.unavailable("TLS: \(failure)") }
    }

    func send(_ data: Data) throws {
        let sem = DispatchSemaphore(value: 0)
        var failure: Error?
        conn.send(content: data, completion: .contentProcessed { failure = $0; sem.signal() })
        sem.wait()
        if let failure { throw failure }
    }

    /// Read exactly `n` bytes (TLS records may split across receives).
    func recvExact(_ n: Int) throws -> Data {
        var out = Data(); out.reserveCapacity(n)
        while out.count < n {
            let want = n - out.count
            let sem = DispatchSemaphore(value: 0)
            var chunk: Data?; var failure: Error?
            conn.receive(minimumIncompleteLength: 1, maximumLength: want) { d, _, isComplete, e in
                chunk = d; failure = e
                if (d == nil || d!.isEmpty) && isComplete && e == nil {
                    failure = TLSError.unavailable("connection closed mid-frame")
                }
                sem.signal()
            }
            sem.wait()
            if let failure { throw failure }
            guard let chunk, !chunk.isEmpty else { throw TLSError.unavailable("empty read") }
            out.append(chunk)
        }
        return out
    }

    func close() { conn.cancel() }
}
