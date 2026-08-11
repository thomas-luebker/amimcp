// AmigaWire.swift — the amimcp wire protocol, spoken natively.
//
// One request per connection, exactly like server/amiga.py: connect, AUTH if
// a token is set, send one frame, read one response, close. Blocking BSD
// sockets on purpose — the protocol is strictly request/response and a
// deterministic timeout beats juggling NWConnection state. Callers hop off
// the main actor via the async wrappers at the bottom.

import Foundation

public enum AmigaWireError: LocalizedError {
    case unreachable(String)
    case refused(String)        // agent answered ST_ERR; message is its text
    case authRequired

    public var errorDescription: String? {
        switch self {
        case .unreachable(let why): return why
        case .refused(let msg): return msg
        case .authRequired: return "agent wants a token (AUTH_REQUIRED)"
        }
    }
}

public struct AmigaScreenshot {
    public let width: Int
    public let height: Int
    /// RGBA8888, width*height*4 — palette already applied.
    public let rgba: [UInt8]
}

public struct AmigaScreenInfo {
    public let index: Int
    public let width: Int
    public let height: Int
    public let depth: Int
    public let title: String
}

public struct AmigaDirEntry: Identifiable, Hashable, Sendable {
    public var id: String { name }
    public let isDir: Bool
    public let size: Int
    public let protection: String
    public let date: String
    public let name: String
}

/// Amiga raw key codes for the keys that have no printable character
/// (printable text goes through TEXT, which uses the Amiga's own keymap).
public let amigaRawkeys: [String: UInt8] = [
    "backspace": 0x41, "tab": 0x42, "enter": 0x43, "return": 0x44,
    "esc": 0x45, "delete": 0x46, "help": 0x5F, "space": 0x40,
    "up": 0x4C, "down": 0x4D, "right": 0x4E, "left": 0x4F,
    "f1": 0x50, "f2": 0x51, "f3": 0x52, "f4": 0x53, "f5": 0x54,
    "f6": 0x55, "f7": 0x56, "f8": 0x57, "f9": 0x58, "f10": 0x59,
]

public struct AmigaClient: Sendable {
    public let host: String
    public let port: UInt16
    public let token: String

    public init(host: String, port: UInt16 = 7846, token: String = "") {
        self.host = host; self.port = port; self.token = token
    }

    // ---- commands --------------------------------------------------------

    static let cmdPing: UInt8 = 0x01
    static let cmdExec: UInt8 = 0x02
    static let cmdGet: UInt8 = 0x03
    static let cmdPut: UInt8 = 0x04
    static let cmdList: UInt8 = 0x05
    static let cmdInfo: UInt8 = 0x06
    static let cmdShot: UInt8 = 0x07
    static let cmdInput: UInt8 = 0x08
    static let cmdScreens: UInt8 = 0x0A
    static let cmdPointer: UInt8 = 0x0B
    static let cmdHash: UInt8 = 0x0C
    static let cmdUITree: UInt8 = 0x0D
    static let cmdUIAct: UInt8 = 0x0E
    static let cmdMenus: UInt8 = 0x0F
    static let cmdAuth: UInt8 = 0x10

    // ---- one transaction -------------------------------------------------

    public func request(_ code: UInt8, _ payload: Data = Data(),
                 timeout: TimeInterval = 10) throws -> Data {
        let fd = try connect(timeout: timeout)
        defer { close(fd) }
        applyIOTimeout(fd, timeout)
        if !token.isEmpty {
            try sendFrame(fd, Self.cmdAuth, Data(token.utf8))
            _ = try readResponse(fd)     // empty OK, or throws
        }
        try sendFrame(fd, code, payload)
        return try readResponse(fd)
    }

    // ---- framing ---------------------------------------------------------

    private func sendFrame(_ fd: Int32, _ code: UInt8, _ payload: Data) throws {
        var frame = Data("AMI0".utf8)
        frame.append(contentsOf: [code, 0, 0, 0])
        var len = UInt32(payload.count).bigEndian
        withUnsafeBytes(of: &len) { frame.append(contentsOf: $0) }
        frame.append(payload)
        try frame.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let n = send(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent, 0)
                guard n > 0 else { throw AmigaWireError.unreachable("send failed: \(errnoText())") }
                sent += n
            }
        }
    }

    private func readResponse(_ fd: Int32) throws -> Data {
        let header = try recvExact(fd, 12)
        guard header.prefix(4) == Data("AMI0".utf8) else {
            throw AmigaWireError.unreachable("bad magic in response")
        }
        let status = header[4]
        let length = header.subdata(in: 8..<12).withUnsafeBytes {
            UInt32(bigEndian: $0.load(as: UInt32.self))
        }
        guard length <= 16 * 1024 * 1024 else {
            throw AmigaWireError.unreachable("response claims \(length) bytes")
        }
        let body = try recvExact(fd, Int(length))
        switch status {
        case 0x00: return body
        case 0x01: throw AmigaWireError.refused(String(decoding: body, as: UTF8.self))
        case 0x02: throw AmigaWireError.authRequired
        default: throw AmigaWireError.unreachable("unknown status \(status)")
        }
    }

    private func recvExact(_ fd: Int32, _ n: Int) throws -> Data {
        var out = Data(capacity: n)
        var buf = [UInt8](repeating: 0, count: 64 * 1024)
        while out.count < n {
            let want = min(buf.count, n - out.count)
            let got = recv(fd, &buf, want, 0)
            guard got > 0 else {
                throw AmigaWireError.unreachable(got == 0 ? "connection closed mid-frame"
                                                          : "recv failed: \(errnoText())")
            }
            out.append(contentsOf: buf[0..<got])
        }
        return out
    }

    // ---- socket plumbing -------------------------------------------------

    private func connect(timeout: TimeInterval) throws -> Int32 {
        var hints = addrinfo(ai_flags: 0, ai_family: AF_INET, ai_socktype: SOCK_STREAM,
                             ai_protocol: IPPROTO_TCP, ai_addrlen: 0, ai_canonname: nil,
                             ai_addr: nil, ai_next: nil)
        var res: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, String(port), &hints, &res) == 0, let info = res else {
            throw AmigaWireError.unreachable("cannot resolve \(host)")
        }
        defer { freeaddrinfo(res) }

        let fd = socket(info.pointee.ai_family, info.pointee.ai_socktype, info.pointee.ai_protocol)
        guard fd >= 0 else { throw AmigaWireError.unreachable("socket: \(errnoText())") }

        // Non-blocking connect with a real deadline, then back to blocking.
        let flags = fcntl(fd, F_GETFL, 0)
        _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
        let rc = Darwin.connect(fd, info.pointee.ai_addr, info.pointee.ai_addrlen)
        if rc != 0 && errno != EINPROGRESS {
            close(fd); throw AmigaWireError.unreachable("connect: \(errnoText())")
        }
        if rc != 0 {
            var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
            let ready = poll(&pfd, 1, Int32(timeout * 1000))
            guard ready == 1 else {
                close(fd); throw AmigaWireError.unreachable("connect timeout to \(host)")
            }
            var err: Int32 = 0
            var len = socklen_t(MemoryLayout<Int32>.size)
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len)
            guard err == 0 else {
                close(fd); throw AmigaWireError.unreachable("connect: \(String(cString: strerror(err)))")
            }
        }
        _ = fcntl(fd, F_SETFL, flags)
        var one: Int32 = 1
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, socklen_t(MemoryLayout<Int32>.size))
        return fd
    }

    private func applyIOTimeout(_ fd: Int32, _ timeout: TimeInterval) {
        var tv = timeval(tv_sec: Int(timeout), tv_usec: Int32((timeout - floor(timeout)) * 1e6))
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    }

    private func errnoText() -> String { String(cString: strerror(errno)) }
}

// ---- async high-level API -----------------------------------------------

extension AmigaClient {
    private func detached<T: Sendable>(_ body: @escaping @Sendable () throws -> T) async throws -> T {
        try await Task.detached(priority: .userInitiated) { try body() }.value
    }

    public func ping(timeout: TimeInterval = 10) async throws -> String {
        let body = try await detached { try request(Self.cmdPing, timeout: timeout) }
        return String(decoding: body, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func info() async throws -> [String: String] {
        let body = try await detached { try request(Self.cmdInfo) }
        var out: [String: String] = [:]
        for line in String(decoding: body, as: UTF8.self).split(separator: "\n") {
            if let eq = line.firstIndex(of: "=") {
                out[String(line[..<eq])] = String(line[line.index(after: eq)...])
            }
        }
        return out
    }

    /// Returns (rc, output). Deadline is the agent-side limit in seconds.
    public func exec(_ command: String, deadline: UInt16 = 60) async throws -> (Int32, String) {
        var built = Data()
        var d = deadline.bigEndian
        withUnsafeBytes(of: &d) { built.append(contentsOf: $0) }
        built.append(Data(command.unicodeScalars.map { UInt8($0.value & 0xFF) }))
        let payload = built
        let body = try await detached {
            try request(Self.cmdExec, payload, timeout: TimeInterval(deadline) + 15)
        }
        guard body.count >= 4 else { throw AmigaWireError.unreachable("short EXEC reply") }
        let rc = body.prefix(4).withUnsafeBytes { Int32(bigEndian: $0.load(as: Int32.self)) }
        return (rc, String(decoding: body.dropFirst(4), as: UTF8.self))
    }

    public func screens() async throws -> [AmigaScreenInfo] {
        let body = try await detached { try request(Self.cmdScreens) }
        var out: [AmigaScreenInfo] = []
        var i = body.startIndex
        func u8() -> Int { defer { i = body.index(after: i) }; return Int(body[i]) }
        func u16() -> Int { let hi = u8(); return hi << 8 | u8() }
        guard body.count >= 1 else { return out }
        let count = u8()
        for _ in 0..<count {
            guard body.distance(from: i, to: body.endIndex) >= 11 else { break }
            let idx = u8(); let w = u16(); let h = u16(); let depth = u8()
            i = body.index(i, offsetBy: 4)          // modeid, unused here
            let tlen = u8()
            let title = String(decoding: body[i..<body.index(i, offsetBy: tlen)], as: UTF8.self)
            i = body.index(i, offsetBy: tlen)
            out.append(AmigaScreenInfo(index: idx, width: w, height: h, depth: depth, title: title))
        }
        return out
    }

    public func regionHash() async throws -> UInt32 {
        let body = try await detached { try request(Self.cmdHash) }
        guard body.count >= 4 else { throw AmigaWireError.unreachable("short HASH reply") }
        return body.prefix(4).withUnsafeBytes { UInt32(bigEndian: $0.load(as: UInt32.self)) }
    }

    // ---- file transfer (GET / PUT / LIST) --------------------------------

    /// A directory listing. `path` is an AmigaDOS dir like `Work:` or `SYS:Tools`.
    public func listDir(_ path: String) async throws -> [AmigaDirEntry] {
        let body = try await detached { try request(Self.cmdList, Data(path.unicodeScalars.map { UInt8($0.value & 0xFF) })) }
        var out: [AmigaDirEntry] = []
        for line in String(decoding: body, as: UTF8.self).split(separator: "\n") {
            // type <TAB> size <TAB> protection <TAB> date <TAB> name  (name last:
            // AmigaDOS names may contain spaces but never tabs).
            let parts = line.split(separator: "\t", maxSplits: 4, omittingEmptySubsequences: false)
            guard parts.count == 5 else { continue }
            out.append(AmigaDirEntry(isDir: parts[0] == "D",
                                     size: Int(parts[1]) ?? 0,
                                     protection: String(parts[2]),
                                     date: String(parts[3]),
                                     name: String(parts[4])))
        }
        out.sort { ($0.isDir ? 0 : 1, $0.name.lowercased()) < ($1.isDir ? 0 : 1, $1.name.lowercased()) }
        return out
    }

    /// Download a file's raw bytes.
    public func getFile(_ path: String, timeout: TimeInterval = 120) async throws -> Data {
        try await detached {
            try request(Self.cmdGet, Data(path.unicodeScalars.map { UInt8($0.value & 0xFF) }), timeout: timeout)
        }
    }

    /// The agent's per-frame ceiling (AMI_MAXFRAME). GET/PUT can't exceed it in
    /// one shot, so larger files are chunked.
    public static let maxFrame = 16 * 1024 * 1024

    /// Upload of any size: a small file goes in one PUT; a large one is split
    /// into ≤ `chunk` parts, each PUT, then reassembled on the Amiga with `Join`.
    /// `progress` is called with 0…1 as parts land.
    public func sendFile(_ path: String, _ data: Data, chunk: Int = 8 * 1024 * 1024,
                         progress: (@Sendable (Double) -> Void)? = nil) async throws {
        if data.count <= chunk {
            try await putFile(path, data, timeout: 300)
            progress?(1)
            return
        }
        let n = (data.count + chunk - 1) / chunk
        var parts: [String] = []
        for i in 0..<n {
            let lo = i * chunk, hi = min(lo + chunk, data.count)
            let part = "\(path).ap\(i)"
            try await putFile(part, data.subdata(in: lo..<hi), timeout: 300)
            parts.append(part)
            progress?(Double(i + 1) / Double(n + 1))       // leave a slice for Join
        }
        let quoted = parts.map { "\"\($0)\"" }.joined(separator: " ")
        let (rc, out) = try await exec("Join \(quoted) AS \"\(path)\"", deadline: 180)
        _ = try? await exec("Delete \(quoted) QUIET", deadline: 120)
        guard rc == 0 else { throw AmigaWireError.refused("Join failed (rc \(rc)): \(out)") }
        progress?(1)
    }

    /// Upload raw bytes to `path` (creating/overwriting the file).
    public func putFile(_ path: String, _ data: Data, timeout: TimeInterval = 120) async throws {
        let pathBytes = Data(path.unicodeScalars.map { UInt8($0.value & 0xFF) })
        var built = Data()
        var len = UInt16(pathBytes.count).bigEndian
        withUnsafeBytes(of: &len) { built.append(contentsOf: $0) }
        built.append(pathBytes)
        built.append(data)
        let payload = built
        _ = try await detached { try request(Self.cmdPut, payload, timeout: timeout) }
    }

    // ---- semantic GUI layer (UITREE / UIACT / MENUS) ---------------------

    /// The frontmost screen's Intuition window/gadget tree, as text (see
    /// PROTOCOL.md UITREE). Parse with `FleetUINode.parse`.
    public func uiTree() async throws -> String {
        let body = try await detached { try request(Self.cmdUITree) }
        return String(decoding: body, as: UTF8.self)
    }

    /// Click a gadget by identity: `gadget` is a GadgetID or a label/role
    /// substring, `window` optionally scopes by title/index. Returns the
    /// agent's short summary; throws `.refused` if nothing matched.
    @discardableResult
    public func uiClick(gadget: String, window: String = "", double: Bool = false) async throws -> String {
        let payload = "\(double ? "dclick" : "click")\t\(window)\t\(gadget)"
        let body = try await detached { try request(Self.cmdUIAct, Data(payload.utf8)) }
        return String(decoding: body, as: UTF8.self)
    }

    /// A window's menu strip as text (default: the active window). See
    /// PROTOCOL.md MENUS.
    public func menus(window: String = "") async throws -> String {
        let body = try await detached { try request(Self.cmdMenus, Data(window.utf8)) }
        return String(decoding: body, as: UTF8.self)
    }

    /// Invoke a menu item by its shortcut character (right-Amiga+char).
    @discardableResult
    public func menuShortcut(_ char: Character) async throws -> String {
        let payload = "menushort\t\t\(char)"
        let body = try await detached { try request(Self.cmdUIAct, Data(payload.utf8)) }
        return String(decoding: body, as: UTF8.self)
    }

    public func screenshot(timeout: TimeInterval = 60) async throws -> AmigaScreenshot {
        let body = try await detached { try request(Self.cmdShot, Data(), timeout: timeout) }
        guard body.count >= 8 else { throw AmigaWireError.unreachable("short SHOT reply") }
        let fmt = body[0]
        let width = Int(body[2]) << 8 | Int(body[3])
        let height = Int(body[4]) << 8 | Int(body[5])
        let ncolors = Int(body[6]) << 8 | Int(body[7])
        var rgba = [UInt8](repeating: 255, count: width * height * 4)

        if fmt == 1 {                                    // palette + chunky
            let palOff = 8
            let pixOff = palOff + ncolors * 3
            guard body.count >= pixOff + width * height else {
                throw AmigaWireError.unreachable("truncated chunky SHOT")
            }
            body.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                let p = raw.bindMemory(to: UInt8.self)
                for i in 0..<(width * height) {
                    let c = Int(p[pixOff + i])
                    let pe = palOff + min(c, max(ncolors - 1, 0)) * 3
                    rgba[i * 4 + 0] = p[pe]
                    rgba[i * 4 + 1] = p[pe + 1]
                    rgba[i * 4 + 2] = p[pe + 2]
                }
            }
        } else if fmt == 2 {                             // packed RGB24
            let pixOff = 8
            guard body.count >= pixOff + width * height * 3 else {
                throw AmigaWireError.unreachable("truncated RGB24 SHOT")
            }
            body.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                let p = raw.bindMemory(to: UInt8.self)
                for i in 0..<(width * height) {
                    rgba[i * 4 + 0] = p[pixOff + i * 3]
                    rgba[i * 4 + 1] = p[pixOff + i * 3 + 1]
                    rgba[i * 4 + 2] = p[pixOff + i * 3 + 2]
                }
            }
        } else {
            throw AmigaWireError.unreachable("unknown SHOT format \(fmt)")
        }
        return AmigaScreenshot(width: width, height: height, rgba: rgba)
    }

    // ---- input injection -------------------------------------------------

    public func click(x: Int, y: Int, button: UInt8 = 0, count: UInt8 = 1) async throws {
        let p = Data([5,                                 // IN_CLICK
                      UInt8(x >> 8), UInt8(x & 0xFF), UInt8(y >> 8), UInt8(y & 0xFF),
                      button, count])
        _ = try await detached { try request(Self.cmdInput, p) }
    }

    public func type(_ text: String) async throws {
        // Latin-1, mapped through the Amiga's own keymap on the far side.
        let bytes = text.unicodeScalars.compactMap { $0.value <= 0xFF ? UInt8($0.value) : nil }
        guard !bytes.isEmpty else { return }
        let p = Data([4] + bytes)                        // IN_TEXT
        _ = try await detached { try request(Self.cmdInput, p) }
    }

    public func pressKey(rawcode: UInt8, qualifier: UInt16 = 0) async throws {
        // One connection per frame, press then release — a press without a
        // release reads as a finger resting on the key (README, 0.4.0 notes).
        for down: UInt8 in [1, 0] {
            let p = Data([3,                             // IN_KEY
                          rawcode, down, UInt8(qualifier >> 8), UInt8(qualifier & 0xFF)])
            _ = try await detached { try request(Self.cmdInput, p) }
        }
    }
}
