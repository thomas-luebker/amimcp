import Foundation
import AmigaDiskKit

// Build an Amiga-readable LHA. Files are added explicitly and in order so the
// archive matches the previous releases' flat layout (no leading directory),
// which is what the amipkg recipe's placeFile ops expect.
let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: lhapack <out.lha> <file>...\n".data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: args[1])
var w = LHAWriter()
for path in args.dropFirst(2) {
    let url = URL(fileURLWithPath: path)
    let data = try Data(contentsOf: url)
    try w.addFile(path: url.lastPathComponent, data: data)
    print("  + \(url.lastPathComponent) (\(data.count) bytes)")
}
try w.finish().write(to: out)
print("wrote \(out.path)")
