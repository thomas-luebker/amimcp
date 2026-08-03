// swift-tools-version: 5.9
import PackageDescription

// Builds an Amiga-readable LHA for releases. macOS ships no LHA *writer* —
// the `lha` in Homebrew is Lhasa, which only extracts — so releases go through
// AmigaDiskKit's LHAWriter. Point the dependency at your AmigaDiskKit checkout.
let package = Package(
    name: "lhapack",
    platforms: [.macOS(.v13)],
    dependencies: [.package(path: "../../../AmigaDiskKit")],
    targets: [.executableTarget(name: "lhapack",
              dependencies: [.product(name: "AmigaDiskKit", package: "AmigaDiskKit")])]
)
