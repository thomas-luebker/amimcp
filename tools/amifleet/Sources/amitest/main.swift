// amitest — headless exerciser for AmigaKit, the exact protocol client the
// amifleet GUI uses. Proves the input path (click / type / rawkey) end to end
// against a real agent, without driving the GUI.
//
//   amitest <host> [token]          run the full input self-test
//   amitest <host> [token] shot out.ppm   just grab a frame to a PPM
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
