// VNCLauncher.swift — the Amiga-side lifecycle for amifleet's native VNC view:
// check whether AmiVNC is installed, install it on request (amipkg), set its
// password, and start it detached. RFBView then renders it natively (RFBClient)
// — no macOS Screen Sharing, which can't complete a session with AmiVNC's
// 2001-era RFB. See amivnc-modern-client-quirks for the gory details.

import AmigaKit

enum VNCLauncher {
    /// AmiVNC's password store tops out at 8 characters, and an exactly-8 value
    /// is stored one short of what a modern client sends — so its VNC-auth DES
    /// key differs and every login is rejected. Keep this ≤ 7 (verified live:
    /// 8-char fails auth outright, 5-char passes).
    static let password = "amiga"
    static let port = 5900
    /// The .020 build runs on every CPU (040/060/Emu68); performance here is
    /// I/O-bound, so we favour compatibility over an 060-tuned binary.
    static let exe = "SYS:Programs/AmiVNC/AmiVNC.020"

    struct Result { let ok: Bool; let message: String }

    /// True when the AmiVNC executable exists (`List` returns rc 0 only then).
    static func isInstalled(_ client: AmigaClient) async -> Bool {
        let (rc, _) = (try? await client.exec("List \(exe) LFORMAT=%N", deadline: 15)) ?? (-1, "")
        return rc == 0
    }

    /// Install AmiVNC via amipkg (on request). Refreshes the catalog first so a
    /// machine that has never seen the `amivnc` package can still find it, then
    /// installs. Both `amipkg` (in C:) and the `AMIPKG:` assign are tried, since
    /// which one resolves varies by machine. Network fetch → generous deadlines.
    static func install(_ client: AmigaClient) async -> Result {
        for tool in ["amipkg", "AMIPKG:amipkg"] {
            _ = try? await client.exec("\(tool) update", deadline: 120)
            let (rc, out) = (try? await client.exec("\(tool) install amivnc", deadline: 180)) ?? (-1, "")
            let installed = await isInstalled(client)
            if rc == 0 || installed {
                return Result(ok: true, message: "AmiVNC installed.")
            }
            // If the tool itself wasn't found, try the next spelling; otherwise
            // report what amipkg said.
            if !out.lowercased().contains("unknown command") && !out.isEmpty && rc != -1 {
                return Result(ok: false, message: "amipkg couldn’t install AmiVNC:\n\(out.prefix(400))")
            }
        }
        return Result(ok: false,
            message: "Couldn’t run amipkg to install AmiVNC. Install it on the Amiga: `amipkg install amivnc`.")
    }

    /// Set the password and start AmiVNC detached, listening on \(port).
    /// Idempotent: if the server is already up (port in use) the second start
    /// just exits harmlessly.
    @discardableResult
    static func startServer(_ client: AmigaClient) async -> Result {
        // 1. Write the password file (AmiVNC -p… sets it and exits).
        _ = try? await client.exec("\(exe) -p\(password)", deadline: 20)
        // 2. Start the server detached on our port. The `<NIL:` is load-bearing:
        //    without a redirected input, AmiVNC's inherited console handle closes
        //    the moment the launching shell returns and the server exits with it.
        //    With `<NIL:` it stays up, listening on \(port) and serving the
        //    frontmost screen. (Verified live on the PiStorm.) `-s` sets the port.
        _ = try? await client.exec("Run >NIL: <NIL: \(exe) -s\(port)", deadline: 10)
        return Result(ok: true, message: "AmiVNC is serving :\(port).")
    }
}
