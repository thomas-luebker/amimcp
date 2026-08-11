// RFBClient.swift — a small, tolerant RFB (VNC) client, just enough to render
// and drive AmiVNC (RFB 3.3, VNC-DES auth, Raw encoding).
//
// Why hand-rolled: macOS Screen Sharing refuses AmiVNC's 2001-era framebuffer
// negotiation. This client does the opposite of Apple's picky viewer — it
// ADAPTS to whatever pixel format the server announces (8/16/32-bpp, true-
// colour OR palette) and advertises only Raw + CopyRect, so the server never
// has to do anything clever. Blocking BSD sockets, the same idiom as
// AmigaWire: connect, read/write a byte stream, close.
//
// Threading: one connection, full-duplex. A reader loop calls `pumpOnce`; input
// (`sendPointer`/`sendKey`) may be called from another thread — all writes take
// `sendLock`, and TCP lets us write while a recv blocks.

import Foundation
#if canImport(CommonCrypto)
import CommonCrypto
#endif

public struct RFBFrame: Sendable {
    public let width: Int
    public let height: Int
    /// RGBA8888, width*height*4.
    public let rgba: [UInt8]
}

public enum RFBError: LocalizedError {
    case unreachable(String)
    case proto(String)
    case authFailed(String)
    public var errorDescription: String? {
        switch self {
        case .unreachable(let s): return s
        case .proto(let s): return "RFB protocol error: \(s)"
        case .authFailed(let s): return "VNC authentication failed: \(s)"
        }
    }
}

public final class RFBClient: @unchecked Sendable {
    public let host: String
    public let port: UInt16
    private var fd: Int32 = -1
    private let sendLock = NSLock()

    // Server pixel format, learned from ServerInit and honoured as-is.
    private var bpp = 32
    private var bigEndian = false
    private var trueColor = true
    private var redMax = 255, greenMax = 255, blueMax = 255
    private var redShift: UInt8 = 16, greenShift: UInt8 = 8, blueShift: UInt8 = 0

    // Palette (for colour-map / indexed servers), R,G,B per entry, 0-255.
    private var palette = [UInt8](repeating: 0, count: 256 * 3)

    public private(set) var width = 0
    public private(set) var height = 0
    public private(set) var name = ""

    /// The server's pixel format, for diagnostics ("why are the colours wrong").
    public var pixelFormat: String {
        "bpp=\(bpp) trueColor=\(trueColor) bigEndian=\(bigEndian) "
        + "max(r,g,b)=(\(redMax),\(greenMax),\(blueMax)) "
        + "shift(r,g,b)=(\(redShift),\(greenShift),\(blueShift))"
    }

    /// Persistent RGBA framebuffer; Raw/CopyRect rects blit into it.
    private var fb = [UInt8]()

    private var bytesPerPixel: Int { bpp / 8 }

    public init(host: String, port: UInt16 = 5900) {
        self.host = host; self.port = port
    }

    deinit { close() }

    // ---- lifecycle -------------------------------------------------------

    /// Connect, negotiate, authenticate. Leaves the client ready to pump.
    public func handshake(password: String, timeout: TimeInterval = 10) throws {
        fd = try openSocket(timeout: timeout)
        // 15s safety timeout on blocking reads; between-message waits use poll().
        var tv = timeval(tv_sec: 15, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        try protocolVersion()
        try security(password: password)
        try clientAndServerInit()
        try setEncodings([0, 1])          // Raw, CopyRect — in preference order
    }

    public func close() {
        if fd >= 0 { Darwin.close(fd); fd = -1 }
    }

    // ---- RFB 3.3 handshake ----------------------------------------------

    private func protocolVersion() throws {
        let hello = try recvExact(12)
        guard hello.prefix(3) == Data("RFB".utf8) else {
            throw RFBError.proto("not an RFB server (got \(hello.prefix(4)))")
        }
        // Force 3.3 — the simplest security flow, and what AmiVNC speaks.
        try writeAll(Data("RFB 003.003\n".utf8))
    }

    private func security(password: String) throws {
        // RFB 3.3: the server dictates one security type as a U32.
        let sectype = try recvU32()
        switch sectype {
        case 0:
            let reason = try recvString()
            throw RFBError.proto("server refused connection: \(reason)")
        case 1:
            break                                   // None — nothing to do
        case 2:
            let challenge = try recvExact(16)
            let response = Self.desResponse(challenge: challenge, password: password)
            try writeAll(response)
            let result = try recvU32()
            if result != 0 {
                // 3.8 servers append a reason; 3.3 usually don't. Best-effort.
                throw RFBError.authFailed("wrong password (server returned \(result))")
            }
        default:
            throw RFBError.proto("unsupported security type \(sectype)")
        }
    }

    private func clientAndServerInit() throws {
        try writeAll(Data([1]))                     // ClientInit: shared = 1
        let info = try recvExact(24)
        width  = Int(info[0]) << 8 | Int(info[1])
        height = Int(info[2]) << 8 | Int(info[3])
        // PIXEL_FORMAT (16 bytes) starts at offset 4.
        bpp        = Int(info[4])
        bigEndian  = info[6] != 0
        trueColor  = info[7] != 0
        redMax     = Int(info[8])  << 8 | Int(info[9])
        greenMax   = Int(info[10]) << 8 | Int(info[11])
        blueMax    = Int(info[12]) << 8 | Int(info[13])
        redShift   = info[14]
        greenShift = info[15]
        blueShift  = info[16]
        let namelen = Int(info[20]) << 24 | Int(info[21]) << 16 | Int(info[22]) << 8 | Int(info[23])
        if namelen > 0, namelen <= 4096 {
            name = String(decoding: try recvExact(namelen), as: UTF8.self)
        }
        guard bpp == 8 || bpp == 16 || bpp == 32, width > 0, height > 0,
              width <= 8192, height <= 8192 else {
            throw RFBError.proto("implausible ServerInit: \(width)x\(height) @\(bpp)bpp")
        }
        fb = [UInt8](repeating: 0, count: width * height * 4)
        // opaque alpha
        for i in 0..<(width * height) { fb[i * 4 + 3] = 255 }
    }

    private func setEncodings(_ encodings: [Int32]) throws {
        var msg = Data([2, 0])                       // type 2, padding
        msg.append(UInt8(encodings.count >> 8)); msg.append(UInt8(encodings.count & 0xFF))
        for e in encodings {
            let u = UInt32(bitPattern: e).bigEndian
            withUnsafeBytes(of: u) { msg.append(contentsOf: $0) }
        }
        try writeAll(msg)
    }

    // ---- the pump --------------------------------------------------------

    /// Ask the server for the next frame. Incremental after the first full one.
    public func requestUpdate(incremental: Bool) throws {
        var msg = Data([3, incremental ? 1 : 0])     // FramebufferUpdateRequest
        for v in [0, 0, width, height] {             // x, y, w, h
            msg.append(UInt8(v >> 8)); msg.append(UInt8(v & 0xFF))
        }
        try writeAll(msg)
    }

    /// Read one server message. Returns a fresh frame when it was a framebuffer
    /// update, `nil` for colour-map/bell/cut-text (state applied, no new frame),
    /// or `nil` when nothing arrived within `timeout` (caller may retry).
    public func pumpOnce(timeout: TimeInterval) throws -> RFBFrame? {
        guard let msgType = try recvTypeByte(timeout) else { return nil }
        switch msgType {
        case 0: return try readFramebufferUpdate()
        case 1: try readColourMap(); return nil
        case 2: return nil                            // Bell
        case 3: try skipCutText(); return nil
        default: throw RFBError.proto("unknown server message \(msgType)")
        }
    }

    /// A snapshot of the current framebuffer (thread-safe copy of the array).
    public func snapshot() -> RFBFrame {
        RFBFrame(width: width, height: height, rgba: fb)
    }

    private func readFramebufferUpdate() throws -> RFBFrame {
        _ = try recvExact(1)                          // padding
        let rects = try recvU16()
        for _ in 0..<rects {
            let x = Int(try recvU16()), y = Int(try recvU16())
            let w = Int(try recvU16()), h = Int(try recvU16())
            let enc = Int32(bitPattern: try recvU32())
            switch enc {
            case 0: try readRaw(x: x, y: y, w: w, h: h)
            case 1: try readCopyRect(x: x, y: y, w: w, h: h)
            default: throw RFBError.proto("unsupported encoding \(enc)")
            }
        }
        return snapshot()
    }

    private func readRaw(x: Int, y: Int, w: Int, h: Int) throws {
        guard w > 0, h > 0 else { return }
        let raw = try recvExact(w * h * bytesPerPixel)
        raw.withUnsafeBytes { (src: UnsafeRawBufferPointer) in
            let p = src.bindMemory(to: UInt8.self)
            var s = 0
            fb.withUnsafeMutableBufferPointer { dst in
                for row in 0..<h {
                    let dy = y + row
                    if dy < 0 || dy >= height { s += w * bytesPerPixel; continue }
                    var d = (dy * width + x) * 4
                    for col in 0..<w {
                        let dx = x + col
                        if dx >= 0 && dx < width {
                            let (r, g, b) = decodePixel(p, s)
                            dst[d] = r; dst[d + 1] = g; dst[d + 2] = b; dst[d + 3] = 255
                        }
                        s += bytesPerPixel
                        d += 4
                    }
                }
            }
        }
    }

    private func readCopyRect(x: Int, y: Int, w: Int, h: Int) throws {
        let sx = Int(try recvU16()), sy = Int(try recvU16())
        guard w > 0, h > 0 else { return }
        // Copy source→dest inside the existing framebuffer. Iterate so that
        // overlapping regions don't clobber (pick direction from the shift).
        let rowsDown = sy < y
        fb.withUnsafeMutableBufferPointer { buf in
            for r in 0..<h {
                let row = rowsDown ? (h - 1 - r) : r
                let syr = sy + row, dyr = y + row
                if syr < 0 || syr >= height || dyr < 0 || dyr >= height { continue }
                for col in 0..<w {
                    let sxc = sx + col, dxc = x + col
                    if sxc < 0 || sxc >= width || dxc < 0 || dxc >= width { continue }
                    let sIdx = (syr * width + sxc) * 4
                    let dIdx = (dyr * width + dxc) * 4
                    buf[dIdx] = buf[sIdx]; buf[dIdx + 1] = buf[sIdx + 1]
                    buf[dIdx + 2] = buf[sIdx + 2]; buf[dIdx + 3] = 255
                }
            }
        }
    }

    /// Decode one pixel at byte offset `off` in `p` to (R,G,B) 0-255.
    private func decodePixel(_ p: UnsafeBufferPointer<UInt8>, _ off: Int) -> (UInt8, UInt8, UInt8) {
        // AmiVNC quirk (verified live on a 68060 + Emu68): it byte-swaps its
        // 32-bit pixels to match the little-endian flag it advertises, but sends
        // 16-bit pixels in the Amiga's native BIG-endian order while STILL
        // flagging little-endian — so a 16-bit screen decodes to a green-cast
        // rainbow unless we read those two bytes big-endian regardless.
        var val: UInt32 = 0
        if bigEndian || bytesPerPixel == 2 {
            for i in 0..<bytesPerPixel { val = (val << 8) | UInt32(p[off + i]) }
        } else {
            for i in 0..<bytesPerPixel { val |= UInt32(p[off + i]) << (8 * i) }
        }
        if trueColor {
            let r = scale((val >> UInt32(redShift))   & UInt32(redMax),   redMax)
            let g = scale((val >> UInt32(greenShift)) & UInt32(greenMax), greenMax)
            let b = scale((val >> UInt32(blueShift))  & UInt32(blueMax),  blueMax)
            return (r, g, b)
        } else {
            let idx = Int(val) & 0xFF
            return (palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2])
        }
    }

    private func scale(_ comp: UInt32, _ max: Int) -> UInt8 {
        if max == 255 { return UInt8(comp & 0xFF) }
        if max <= 0 { return 0 }
        return UInt8(min(255, Int(comp) * 255 / max))
    }

    private func readColourMap() throws {
        _ = try recvExact(1)                          // padding
        let first = Int(try recvU16())
        let count = Int(try recvU16())
        let body = try recvExact(count * 6)           // U16 R,G,B each
        body.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let p = raw.bindMemory(to: UInt8.self)
            for i in 0..<count {
                let idx = first + i
                guard idx >= 0 && idx < 256 else { continue }
                // 16-bit intensities → 8-bit (high byte).
                palette[idx * 3]     = p[i * 6]
                palette[idx * 3 + 1] = p[i * 6 + 2]
                palette[idx * 3 + 2] = p[i * 6 + 4]
            }
        }
    }

    private func skipCutText() throws {
        _ = try recvExact(3)                          // padding
        let len = Int(try recvU32())
        if len > 0 { _ = try recvExact(min(len, 1 << 20)) }
    }

    // ---- input -----------------------------------------------------------

    /// buttonMask: bit0 = left, bit1 = middle, bit2 = right.
    public func sendPointer(x: Int, y: Int, buttonMask: UInt8) throws {
        let cx = max(0, min(x, width - 1)), cy = max(0, min(y, height - 1))
        var msg = Data([5, buttonMask])
        msg.append(UInt8(cx >> 8)); msg.append(UInt8(cx & 0xFF))
        msg.append(UInt8(cy >> 8)); msg.append(UInt8(cy & 0xFF))
        try writeAll(msg)
    }

    public func sendKey(keysym: UInt32, down: Bool) throws {
        var msg = Data([4, down ? 1 : 0, 0, 0])
        let k = keysym.bigEndian
        withUnsafeBytes(of: k) { msg.append(contentsOf: $0) }
        try writeAll(msg)
    }

    // ---- VNC DES auth ----------------------------------------------------

    /// The classic VNC quirk: DES-ECB-encrypt the 16-byte challenge with the
    /// password (8 bytes, null-padded) as key, but each key byte's bits are
    /// mirrored first.
    static func desResponse(challenge: Data, password: String) -> Data {
        var key = [UInt8](repeating: 0, count: 8)
        let pw = Array(password.utf8.prefix(8))
        for i in 0..<pw.count { key[i] = mirror(pw[i]) }
        var out = [UInt8](repeating: 0, count: 16)
        #if canImport(CommonCrypto)
        var moved = 0
        let src = [UInt8](challenge)
        _ = key.withUnsafeBytes { kp in
            src.withUnsafeBytes { sp in
                out.withUnsafeMutableBytes { op in
                    CCCrypt(CCOperation(kCCEncrypt), CCAlgorithm(kCCAlgorithmDES),
                            CCOptions(kCCOptionECBMode),
                            kp.baseAddress, 8, nil,
                            sp.baseAddress, 16,
                            op.baseAddress, 16, &moved)
                }
            }
        }
        #endif
        return Data(out)
    }

    private static func mirror(_ b: UInt8) -> UInt8 {
        var v = b, r: UInt8 = 0
        for _ in 0..<8 { r = (r << 1) | (v & 1); v >>= 1 }
        return r
    }

    // ---- socket + byte-stream plumbing -----------------------------------

    private func writeAll(_ data: Data) throws {
        sendLock.lock(); defer { sendLock.unlock() }
        try data.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let n = send(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent, 0)
                guard n > 0 else { throw RFBError.unreachable("send failed: \(errnoText())") }
                sent += n
            }
        }
    }

    /// Wait up to `timeout` for the next message-type byte; nil if none arrived.
    private func recvTypeByte(_ timeout: TimeInterval) throws -> UInt8? {
        var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
        let ready = poll(&pfd, 1, Int32(timeout * 1000))
        if ready == 0 { return nil }
        guard ready == 1 else { throw RFBError.unreachable("poll: \(errnoText())") }
        var b: UInt8 = 0
        let n = recv(fd, &b, 1, 0)
        if n == 1 { return b }
        if n == 0 { throw RFBError.unreachable("server closed the connection") }
        throw RFBError.unreachable("recv: \(errnoText())")
    }

    private func recvExact(_ n: Int) throws -> Data {
        guard n > 0 else { return Data() }
        var out = Data(capacity: n)
        var buf = [UInt8](repeating: 0, count: 64 * 1024)
        while out.count < n {
            let want = min(buf.count, n - out.count)
            let got = recv(fd, &buf, want, 0)
            guard got > 0 else {
                throw RFBError.unreachable(got == 0 ? "connection closed mid-message"
                                                    : "recv: \(errnoText())")
            }
            out.append(contentsOf: buf[0..<got])
        }
        return out
    }

    private func recvU16() throws -> Int {
        let d = try recvExact(2); return Int(d[0]) << 8 | Int(d[1])
    }
    private func recvU32() throws -> UInt32 {
        let d = try recvExact(4)
        return UInt32(d[0]) << 24 | UInt32(d[1]) << 16 | UInt32(d[2]) << 8 | UInt32(d[3])
    }
    private func recvString() throws -> String {
        let len = Int(try recvU32())
        guard len > 0, len <= 4096 else { return "" }
        return String(decoding: try recvExact(len), as: UTF8.self)
    }

    private func openSocket(timeout: TimeInterval) throws -> Int32 {
        var hints = addrinfo(ai_flags: 0, ai_family: AF_INET, ai_socktype: SOCK_STREAM,
                             ai_protocol: IPPROTO_TCP, ai_addrlen: 0, ai_canonname: nil,
                             ai_addr: nil, ai_next: nil)
        var res: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, String(port), &hints, &res) == 0, let info = res else {
            throw RFBError.unreachable("cannot resolve \(host)")
        }
        defer { freeaddrinfo(res) }
        let s = socket(info.pointee.ai_family, info.pointee.ai_socktype, info.pointee.ai_protocol)
        guard s >= 0 else { throw RFBError.unreachable("socket: \(errnoText())") }
        let flags = fcntl(s, F_GETFL, 0)
        _ = fcntl(s, F_SETFL, flags | O_NONBLOCK)
        let rc = Darwin.connect(s, info.pointee.ai_addr, info.pointee.ai_addrlen)
        if rc != 0 && errno != EINPROGRESS {
            Darwin.close(s); throw RFBError.unreachable("connect: \(errnoText())")
        }
        if rc != 0 {
            var pfd = pollfd(fd: s, events: Int16(POLLOUT), revents: 0)
            guard poll(&pfd, 1, Int32(timeout * 1000)) == 1 else {
                Darwin.close(s); throw RFBError.unreachable("connect timeout to \(host):\(port)")
            }
            var err: Int32 = 0
            var len = socklen_t(MemoryLayout<Int32>.size)
            getsockopt(s, SOL_SOCKET, SO_ERROR, &err, &len)
            guard err == 0 else {
                Darwin.close(s); throw RFBError.unreachable("connect: \(String(cString: strerror(err)))")
            }
        }
        _ = fcntl(s, F_SETFL, flags)
        var one: Int32 = 1
        setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, socklen_t(MemoryLayout<Int32>.size))
        return s
    }

    private func errnoText() -> String { String(cString: strerror(errno)) }
}
