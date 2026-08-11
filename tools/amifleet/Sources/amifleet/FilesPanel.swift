// FilesPanel.swift — the Amiga → Mac half of the VNC window's file transfer.
// Browse the Amiga's drives (LIST), then drag a file straight to the Finder or
// hit Save — the bytes come down over the agent's GET on demand.

import SwiftUI
import AppKit
import UniformTypeIdentifiers
import AmigaKit

@MainActor
final class FilesModel: ObservableObject {
    @Published var path: String
    @Published var entries: [AmigaDirEntry] = []
    @Published var status = ""
    @Published var loading = false

    let client: AmigaClient

    init(client: AmigaClient, start: String = "SYS:") {
        self.client = client
        self.path = start
    }

    func load() {
        loading = true; status = "reading \(path)…"
        Task {
            do {
                let list = try await client.listDir(path)
                entries = list
                status = "\(list.count) items"
            } catch {
                entries = []
                status = "can’t read \(path): \(error.localizedDescription)"
            }
            loading = false
        }
    }

    func open(_ e: AmigaDirEntry) {
        guard e.isDir else { return }
        path = Self.join(path, e.name); load()
    }

    func up() {
        path = Self.parent(path); load()
    }

    func go(to newPath: String) {
        let p = newPath.trimmingCharacters(in: .whitespaces)
        guard !p.isEmpty else { return }
        path = p; load()
    }

    /// Lazy drag-to-Finder: the file downloads only if the drop is accepted.
    func itemProvider(for e: AmigaDirEntry) -> NSItemProvider {
        let full = Self.join(path, e.name)
        let client = self.client
        let provider = NSItemProvider()
        provider.suggestedName = e.name
        provider.registerFileRepresentation(forTypeIdentifier: UTType.data.identifier,
                                             fileOptions: [], visibility: .all) { completion in
            Task {
                do {
                    let data = try await client.getFile(full, timeout: 300)
                    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(e.name)
                    try? FileManager.default.removeItem(at: tmp)
                    try data.write(to: tmp)
                    completion(tmp, false, nil)
                } catch {
                    completion(nil, false, error)
                }
            }
            return nil
        }
        return provider
    }

    /// Save-panel fallback (and the obvious button for people who don't drag).
    func save(_ e: AmigaDirEntry) {
        let full = Self.join(path, e.name)
        let panel = NSSavePanel()
        panel.nameFieldStringValue = e.name
        guard panel.runModal() == .OK, let url = panel.url else { return }
        status = "downloading \(e.name)…"
        Task {
            do {
                let data = try await client.getFile(full, timeout: 300)
                try data.write(to: url)
                status = "saved \(e.name) (\(data.count) bytes)"
            } catch {
                status = "download failed: \(error.localizedDescription)"
            }
        }
    }

    // AmigaDOS path helpers: "SYS:" + "Tools" → "SYS:Tools"; nested → "…/name".
    static func join(_ base: String, _ name: String) -> String {
        base.hasSuffix(":") || base.hasSuffix("/") ? base + name : "\(base)/\(name)"
    }
    static func parent(_ p: String) -> String {
        guard let colon = p.firstIndex(of: ":") else { return p }
        let after = p.index(after: colon)
        let tail = p[after...]
        if tail.isEmpty { return p }                            // already at a volume root
        if let slash = tail.lastIndex(of: "/") {
            return String(p[..<slash])                          // drop last drawer
        }
        return String(p[...colon])                              // back to "Vol:"
    }
}

struct FilesPanel: View {
    @StateObject private var model: FilesModel
    @State private var pathField: String

    init(client: AmigaClient) {
        _model = StateObject(wrappedValue: FilesModel(client: client))
        _pathField = State(initialValue: "SYS:")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Files").font(WB.topaz(11)).bold().foregroundColor(.white)

            HStack(spacing: 4) {
                TextField("Vol:path", text: $pathField, onCommit: { model.go(to: pathField) })
                    .textFieldStyle(.plain).font(WB.topaz(10))
                    .padding(3).background(WB.gray).foregroundColor(.black)
                Button("↑") { model.up(); pathField = model.path }.buttonStyle(WBButtonStyle())
                Button("⟳") { model.load() }.buttonStyle(WBButtonStyle())
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 1) {
                    ForEach(model.entries) { e in
                        FileRow(entry: e,
                                onOpen: { model.open(e); pathField = model.path },
                                provider: { model.itemProvider(for: e) },
                                onSave: { model.save(e) })
                    }
                }
            }
            .background(WB.darkEdge.opacity(0.25))

            Text(model.status).font(WB.topaz(9)).foregroundColor(.white.opacity(0.7)).lineLimit(1)
        }
        .padding(8)
        .frame(width: 260)
        .background(WB.blue.opacity(0.5))
        .onAppear { model.load() }
    }
}

private struct FileRow: View {
    let entry: AmigaDirEntry
    let onOpen: () -> Void
    let provider: () -> NSItemProvider
    let onSave: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Text(entry.isDir ? "📁" : "📄").font(.system(size: 10))
            Text(entry.name).font(WB.topaz(10)).foregroundColor(.white).lineLimit(1)
            Spacer(minLength: 0)
            if entry.isDir {
                Text("").frame(width: 0)
            } else {
                Text(byteSize(entry.size)).font(WB.topaz(9)).foregroundColor(.white.opacity(0.6))
                Button("⤓") { onSave() }.buttonStyle(WBButtonStyle())
                    .help("Save to the Mac")
            }
        }
        .padding(.horizontal, 4).padding(.vertical, 2)
        .contentShape(Rectangle())
        .onTapGesture { if entry.isDir { onOpen() } }
        .ifFile(entry.isDir == false) { view in
            view.onDrag(provider)                           // drag a file to Finder
        }
    }

    private func byteSize(_ n: Int) -> String {
        if n >= 1024 * 1024 { return "\(n / (1024 * 1024)) MB" }
        if n >= 1024 { return "\(n / 1024) KB" }
        return "\(n) B"
    }
}

private extension View {
    /// Apply a modifier only for file rows (dirs aren't draggable).
    @ViewBuilder func ifFile(_ cond: Bool, _ transform: (Self) -> some View) -> some View {
        if cond { transform(self) } else { self }
    }
}
