// swift-tools-version:5.9
// amifleet — an Apple-Remote-Desktop-style console for every Amiga on the LAN.
// Speaks the amimcp wire protocol (PROTOCOL.md) natively; needs no MCP server.
//
// AmigaKit is the protocol client, shared by the GUI (amifleet) and a headless
// CLI harness (amitest) — so the input/screen path can be tested against real
// machines without driving the GUI.
import PackageDescription

let package = Package(
    name: "amifleet",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "AmigaKit", path: "Sources/AmigaKit"),
        .executableTarget(name: "amifleet", dependencies: ["AmigaKit"],
                          path: "Sources/amifleet"),
        .executableTarget(name: "amitest", dependencies: ["AmigaKit"],
                          path: "Sources/amitest"),
    ]
)
