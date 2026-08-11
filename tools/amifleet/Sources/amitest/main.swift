// amitest — headless exerciser for AmigaKit, the exact protocol client the
// amifleet GUI uses. Proves the input path (click / type / rawkey) end to end
// against a real agent, without driving the GUI.
//
//   amitest <host> [token]          run the full input self-test
//   amitest <host> [token] shot out.ppm   just grab a frame to a PPM
//   amitest <host> [token] rfb [out.ppm] [pw] [port]      grab one VNC frame
//                                          (embedded RFBClient → AmiVNC)
//   amitest <host> [token] rfbdrive [out.ppm] [pw] [port] drive the pointer and
//                                          prove the incremental+input path
//
// The self-test opens a shell window on the Amiga, clicks into it to focus,
// types a marker command and presses Return. Verifying the visible result is
// done by the caller (screenshot the Amiga) — this side reports each step.

import Foundation
import AmigaKit

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write(Data("usage: amitest <host> [token] [shot <file.ppm>]\n".utf8))
    exit(2)
}
let host = args[1]
let token = args.count >= 3 && args[2] != "shot" ? args[2] : "a4000"
let client = AmigaClient(host: host, port: 7846, token: token)

func log(_ s: String) { print("[\(host)] \(s)") }

// PPM is the least-code lossless format that needs no dependencies; the caller
// can `sips` it to PNG. Kept for eyeballing a capture from the CLI.
func writePPM(_ shot: AmigaScreenshot, to path: String) throws {
    var out = Data("P6\n\(shot.width) \(shot.height)\n255\n".utf8)
    out.reserveCapacity(out.count + shot.width * shot.height * 3)
    for i in 0..<(shot.width * shot.height) {
        out.append(shot.rgba[i*4]); out.append(shot.rgba[i*4+1]); out.append(shot.rgba[i*4+2])
    }
    try out.write(to: URL(fileURLWithPath: path))
}

let sem = DispatchSemaphore(value: 0)
var failed = false

Task {
    defer { sem.signal() }
    do {
        // shot-only mode
        if let idx = args.firstIndex(of: "shot"), args.count > idx + 1 {
            let shot = try await client.screenshot()
            try writePPM(shot, to: args[idx + 1])
            log("wrote \(shot.width)×\(shot.height) frame to \(args[idx + 1])")
            return
        }

        // rfb mode: connect straight to AmiVNC (port 5900) with the embedded
        // RFB client, pull one frame, and save it — proves the VNC path end to
        // end without the GUI. Usage: amitest <host> [token] rfb [out.ppm] [pw]
        if let idx = args.firstIndex(of: "rfb") {
            let outPath = args.count > idx + 1 && !args[idx + 1].hasPrefix("-") ? args[idx + 1] : "rfb.ppm"
            let pw = args.count > idx + 2 ? args[idx + 2] : "amiga"
            let rfbPort = args.count > idx + 3 ? (UInt16(args[idx + 3]) ?? 5900) : 5900
            let rfb = RFBClient(host: host, port: rfbPort)
            log("connecting to \(host):\(rfbPort) …")
            try rfb.handshake(password: pw)
            log("connected: \(rfb.width)×\(rfb.height), server=\"\(rfb.name)\"")
            try rfb.requestUpdate(incremental: false)
            var frame: RFBFrame?
            for _ in 0..<40 {                        // up to ~20s for the first frame
                if let f = try rfb.pumpOnce(timeout: 0.5) { frame = f; break }
            }
            rfb.close()
            guard let f = frame else { log("no frame arrived"); failed = true; return }
            // RGBA → PPM
            var out = Data("P6\n\(f.width) \(f.height)\n255\n".utf8)
            out.reserveCapacity(out.count + f.width * f.height * 3)
            for i in 0..<(f.width * f.height) {
                out.append(f.rgba[i*4]); out.append(f.rgba[i*4+1]); out.append(f.rgba[i*4+2])
            }
            try out.write(to: URL(fileURLWithPath: outPath))
            log("wrote \(f.width)×\(f.height) VNC frame to \(outPath)")
            return
        }

        // rfbdrive mode: prove the exact code the GUI uses — the incremental
        // update loop plus live pointer input. Move the Amiga pointer over RFB,
        // then confirm incremental frames arrive (the moving pointer changes the
        // screen). Usage: amitest <host> [token] rfbdrive [out.ppm] [pw] [port]
        if let idx = args.firstIndex(of: "rfbdrive") {
            let outPath = args.count > idx + 1 && !args[idx + 1].hasPrefix("-") ? args[idx + 1] : "rfbdrive.ppm"
            let pw = args.count > idx + 2 ? args[idx + 2] : "amiga"
            let port = args.count > idx + 3 ? (UInt16(args[idx + 3]) ?? 5900) : 5900
            let rfb = RFBClient(host: host, port: port)
            try rfb.handshake(password: pw)
            log("connected: \(rfb.width)×\(rfb.height)")
            try rfb.requestUpdate(incremental: false)
            _ = try rfb.pumpOnce(timeout: 3)                       // drain first full frame
            // Drive the pointer to a few spots (buttonMask 0 = just move).
            let spots = [(200, 150), (640, 360), (1000, 600), (rfb.width/2, rfb.height/2)]
            for (x, y) in spots {
                try rfb.sendPointer(x: x, y: y, buttonMask: 0)
                usleep(150_000)
            }
            // A left click at centre, press then release.
            try rfb.sendPointer(x: rfb.width/2, y: rfb.height/2, buttonMask: 1)
            usleep(80_000)
            try rfb.sendPointer(x: rfb.width/2, y: rfb.height/2, buttonMask: 0)
            // Pump incremental frames for a few seconds; count what arrives.
            var frames = 0; var last: RFBFrame?
            try rfb.requestUpdate(incremental: true)
            for _ in 0..<20 {
                if let f = try rfb.pumpOnce(timeout: 0.5) {
                    frames += 1; last = f
                    try rfb.requestUpdate(incremental: true)
                }
            }
            rfb.close()
            log("incremental frames received after input: \(frames)")
            if let f = last {
                var out = Data("P6\n\(f.width) \(f.height)\n255\n".utf8)
                for i in 0..<(f.width * f.height) {
                    out.append(f.rgba[i*4]); out.append(f.rgba[i*4+1]); out.append(f.rgba[i*4+2])
                }
                try out.write(to: URL(fileURLWithPath: outPath))
                log("saved post-input frame to \(outPath)")
            }
            if frames == 0 { log("WARNING: no incremental frames — input or stream may be stuck"); failed = true }
            return
        }

        // rfbfmt mode: connect, print ONLY the server's pixel format, and close.
        // No frame is pulled or saved — a screen-content-free diagnostic for the
        // "wrong colours on some RTG modes" bug. Usage:
        //   amitest <host> [token] rfbfmt [pw] [port]
        if let idx = args.firstIndex(of: "rfbfmt") {
            let pw = args.count > idx + 1 ? args[idx + 1] : "amiga"
            let port = args.count > idx + 2 ? (UInt16(args[idx + 2]) ?? 5900) : 5900
            let rfb = RFBClient(host: host, port: port)
            try rfb.handshake(password: pw)
            log("\(rfb.width)×\(rfb.height)  \(rfb.pixelFormat)")
            rfb.close()
            return
        }

        // tree mode: fetch + parse the UITREE through AmigaKit (same path the
        // inspector uses) and print the model.
        if args.contains("tree") {
            let tree = FleetUITree.parse(try await client.uiTree())
            log("screen: \(tree.screen), \(tree.windows.count) window(s)")
            for win in tree.windows {
                log("  W \(win.displayTitle)\(win.active ? " [active]" : "") "
                    + "@\(win.x),\(win.y) \(win.w)×\(win.h) — \(win.gadgets.count) gadget(s)")
                for g in win.gadgets.prefix(12) {
                    log("    G#\(g.gadgetID) \(g.kind) \"\(g.display)\" -> selector \"\(g.selector)\"")
                }
            }
            return
        }

        log("ping: \(try await client.ping())")

        // A shell window at a known, on-screen rectangle so the click lands in it
        // and its output is visible in a screenshot. Marker is unique per run so
        // we know it came from THIS test (index passed in via the command).
        let marker = "AMIFLEET_OK_\(Int(Date().timeIntervalSince1970) % 100000)"
        let con = "CON:60/60/560/220/amifleet input test/CLOSE"
        _ = try await client.exec("Run >NIL: NewShell \"\(con)\"", deadline: 10)
        log("opened shell window \(con)")
        try await Task.sleep(nanoseconds: 1_500_000_000)   // let it open + activate

        // Click inside the window to make sure it has input focus.
        try await client.click(x: 120, y: 100, button: 0, count: 1)
        log("clicked at (120,100) to focus the window")
        try await Task.sleep(nanoseconds: 400_000_000)

        // Type a command, then Return — exercises IN_TEXT and IN_KEY(return).
        try await client.type("Echo \"\(marker)\"")
        log("typed: Echo \"\(marker)\"")
        try await Task.sleep(nanoseconds: 300_000_000)
        try await client.pressKey(rawcode: amigaRawkeys["return"]!)
        log("pressed Return — the shell should now show \(marker)")
        try await Task.sleep(nanoseconds: 800_000_000)

        // Grab the frame so the run is self-verifying: search the captured
        // pixels is overkill, but confirm the capture path still works too.
        let shot = try await client.screenshot()
        log("post-input frame: \(shot.width)×\(shot.height) captured OK")
        log("MARKER=\(marker)")
    } catch {
        failed = true
        log("FAILED: \(error.localizedDescription)")
    }
}

sem.wait()
exit(failed ? 1 : 0)
