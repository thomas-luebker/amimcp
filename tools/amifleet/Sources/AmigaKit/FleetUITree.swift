// FleetUITree.swift — parse the agent's UITREE text into a model the inspector
// can list. Line formats (PROTOCOL.md):
//   S 0 WxH depth=D "title"
//   W idx X Y WxH state "title"
//   G id X Y WxH kind state "label" ["value"]

import Foundation

public struct FleetUIGadget: Identifiable, Hashable {
    public let id = UUID()
    public let gadgetID: Int
    public let x, y, w, h: Int
    public let kind: String
    public let state: String
    public let label: String
    public let value: String
    /// A stable selector for uiClick: prefer the label, else the role, else the id.
    public var selector: String {
        if !label.isEmpty { return label }
        if kind != "gadget" { return kind }
        return String(gadgetID)
    }
    public var display: String {
        var s = label.isEmpty ? "(\(kind))" : label
        if !value.isEmpty { s += " = \(value)" }
        return s
    }
}

public struct FleetUIWindow: Identifiable, Hashable {
    public let id = UUID()
    public let index: Int
    public let x, y, w, h: Int
    public let active: Bool
    public let title: String
    public var gadgets: [FleetUIGadget]
    public var displayTitle: String { title.isEmpty ? "(untitled window \(index))" : title }
}

public struct FleetUITree {
    public var screen: String
    public var windows: [FleetUIWindow]

    public init(screen: String, windows: [FleetUIWindow]) {
        self.screen = screen
        self.windows = windows
    }

    public static func parse(_ text: String) -> FleetUITree {
        var screen = ""
        var windows: [FleetUIWindow] = []
        for rawLine in text.split(separator: "\n") {
            let line = String(rawLine)
            guard let tag = line.first else { continue }
            let quotes = Self.quoted(line)
            let head = String(line.prefix { $0 != "\"" })
            let toks = head.split(separator: " ", omittingEmptySubsequences: true).map(String.init)

            switch tag {
            case "S":
                screen = quotes.first ?? ""
            case "W" where toks.count >= 6:
                let (w, h) = Self.wh(toks[4])
                windows.append(FleetUIWindow(
                    index: Int(toks[1]) ?? 0,
                    x: Int(toks[2]) ?? 0, y: Int(toks[3]) ?? 0, w: w, h: h,
                    active: toks[5] == "active",
                    title: quotes.first ?? "", gadgets: []))
            case "G" where toks.count >= 7 && !windows.isEmpty:
                let (w, h) = Self.wh(toks[4])
                let g = FleetUIGadget(
                    gadgetID: Int(toks[1]) ?? 0,
                    x: Int(toks[2]) ?? 0, y: Int(toks[3]) ?? 0, w: w, h: h,
                    kind: toks[5], state: toks[6],
                    label: quotes.count > 0 ? quotes[0] : "",
                    value: quotes.count > 1 ? quotes[1] : "")
                windows[windows.count - 1].gadgets.append(g)
            default:
                break
            }
        }
        return FleetUITree(screen: screen, windows: windows)
    }

    /// Every "…" quoted run in a line, in order.
    private static func quoted(_ line: String) -> [String] {
        var out: [String] = []
        var inside = false
        var cur = ""
        for c in line {
            if c == "\"" {
                if inside { out.append(cur); cur = "" }
                inside.toggle()
            } else if inside {
                cur.append(c)
            }
        }
        return out
    }

    /// "WxH" → (W, H).
    private static func wh(_ s: String) -> (Int, Int) {
        let parts = s.split(separator: "x")
        guard parts.count == 2 else { return (0, 0) }
        return (Int(parts[0]) ?? 0, Int(parts[1]) ?? 0)
    }
}
