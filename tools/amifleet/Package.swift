// swift-tools-version:5.9
// amifleet — an Apple-Remote-Desktop-style console for every Amiga on the LAN.
// Speaks the amimcp wire protocol (PROTOCOL.md) natively; needs no MCP server.
import PackageDescription

let package = Package(
    name: "amifleet",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "amifleet", path: "Sources/amifleet")
    ]
)
