// makeicon.swift — renders amifleet's app icon at 1024×1024 to a PNG.
// The icon IS the app: a chunky retro monitor showing the fleet board —
// a Workbench-blue title strip and a row of "machine online" lights — set on
// a rounded macOS tile. Run: swift makeicon.swift out.png
import AppKit

let S: CGFloat = 1024
let outPath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"

guard let ctx = CGContext(data: nil, width: Int(S), height: Int(S),
                          bitsPerComponent: 8, bytesPerRow: 0,
                          space: CGColorSpaceCreateDeviceRGB(),
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    fatalError("no context")
}

func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor {
    CGColor(red: r/255, green: g/255, blue: b/255, alpha: a)
}
// Palette: Workbench blue + grey, retro-beige monitor, the app's status colors.
let wbBlueDark = rgb(0, 44, 92)
let wbBlue     = rgb(0, 84, 170)
let wbGrey     = rgb(168, 168, 168)
let beige      = rgb(216, 206, 180)
let beigeDark  = rgb(150, 140, 116)
let ink        = rgb(28, 28, 30)
let green      = rgb(64, 200, 96)
let amber      = rgb(255, 184, 51)
let white      = rgb(245, 246, 250)

func roundedRect(_ r: CGRect, _ radius: CGFloat) -> CGPath {
    CGPath(roundedRect: r, cornerWidth: radius, cornerHeight: radius, transform: nil)
}

// ---- 1. rounded tile with a vertical gradient ---------------------------
let tile = CGRect(x: 0, y: 0, width: S, height: S).insetBy(dx: 24, dy: 24)
ctx.saveGState()
ctx.addPath(roundedRect(tile, S * 0.225))     // macOS-ish corner radius
ctx.clip()
let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                      colors: [wbBlue, wbBlueDark] as CFArray,
                      locations: [0, 1])!
ctx.drawLinearGradient(grad, start: CGPoint(x: 0, y: S), end: CGPoint(x: 0, y: 0), options: [])
// faint top sheen
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.08))
ctx.fill(CGRect(x: tile.minX, y: S * 0.62, width: tile.width, height: S * 0.38))
ctx.restoreGState()

// ---- 2. the monitor -----------------------------------------------------
// Bezel (beige CRT), centered, with a little stand.
let monW = S * 0.60, monH = S * 0.46
let monX = (S - monW) / 2, monY = S * 0.34
let bezel = CGRect(x: monX, y: monY, width: monW, height: monH)

// stand
let neckW = monW * 0.16, neckH = S * 0.055
ctx.setFillColor(beigeDark)
ctx.fill(CGRect(x: S/2 - neckW/2, y: monY - neckH, width: neckW, height: neckH + 20))
let baseW = monW * 0.42, baseH = S * 0.035
ctx.addPath(roundedRect(CGRect(x: S/2 - baseW/2, y: monY - neckH - baseH,
                               width: baseW, height: baseH), baseH/2))
ctx.setFillColor(beige); ctx.fillPath()

// bezel body with a soft drop shadow
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -18), blur: 40,
              color: CGColor(red: 0, green: 0, blue: 0, alpha: 0.35))
ctx.addPath(roundedRect(bezel, S * 0.045))
ctx.setFillColor(beige); ctx.fillPath()
ctx.restoreGState()
// bezel top highlight
ctx.addPath(roundedRect(bezel, S * 0.045)); ctx.setStrokeColor(white.copy(alpha: 0.4)!)
ctx.setLineWidth(4); ctx.strokePath()

// ---- 3. the screen: a tiny amifleet fleet board -------------------------
let pad = monW * 0.075
let screen = bezel.insetBy(dx: pad, dy: pad)
ctx.addPath(roundedRect(screen, S * 0.02)); ctx.clip()
ctx.setFillColor(wbGrey); ctx.fill(screen)

// blue title strip
let strip = CGRect(x: screen.minX, y: screen.maxY - screen.height * 0.20,
                   width: screen.width, height: screen.height * 0.20)
ctx.setFillColor(wbBlue); ctx.fill(strip)
// three "machine" lights on the strip: green, green, amber
let lightR = strip.height * 0.22
for (i, c) in [green, green, amber].enumerated() {
    let cx = strip.minX + strip.width * (0.16 + 0.16 * CGFloat(i))
    let cy = strip.midY
    ctx.setFillColor(c)
    ctx.fillEllipse(in: CGRect(x: cx - lightR, y: cy - lightR, width: lightR*2, height: lightR*2))
    ctx.setStrokeColor(ink.copy(alpha: 0.5)!); ctx.setLineWidth(3)
    ctx.strokeEllipse(in: CGRect(x: cx - lightR, y: cy - lightR, width: lightR*2, height: lightR*2))
}

// two tiles below the strip (the fleet board), embossed
func embossedTile(_ r: CGRect) {
    ctx.setFillColor(wbGrey); ctx.fill(r)
    ctx.setStrokeColor(white.copy(alpha: 0.9)!); ctx.setLineWidth(4)
    ctx.beginPath(); ctx.move(to: CGPoint(x: r.minX, y: r.minY))
    ctx.addLine(to: CGPoint(x: r.minX, y: r.maxY)); ctx.addLine(to: CGPoint(x: r.maxX, y: r.maxY))
    ctx.strokePath()
    ctx.setStrokeColor(ink.copy(alpha: 0.55)!)
    ctx.beginPath(); ctx.move(to: CGPoint(x: r.maxX, y: r.maxY))
    ctx.addLine(to: CGPoint(x: r.maxX, y: r.minY)); ctx.addLine(to: CGPoint(x: r.minX, y: r.minY))
    ctx.strokePath()
    // a couple of "text" rows
    ctx.setFillColor(ink.copy(alpha: 0.45)!)
    for k in 0..<2 {
        ctx.fill(CGRect(x: r.minX + r.width*0.14, y: r.minY + r.height*(0.35 + 0.28*CGFloat(k)),
                        width: r.width*0.6, height: r.height*0.10))
    }
    // little monitor glyph
    ctx.setFillColor(wbBlue)
    ctx.fill(CGRect(x: r.minX + r.width*0.14, y: r.minY + r.height*0.28,
                    width: r.width*0.16, height: r.height*0.16))
}
let bodyTop = strip.minY
let gap = screen.width * 0.04
let tileW = (screen.width - gap*3) / 2
let tileH = (bodyTop - screen.minY) - gap*2
embossedTile(CGRect(x: screen.minX + gap, y: screen.minY + gap, width: tileW, height: tileH))
embossedTile(CGRect(x: screen.minX + gap*2 + tileW, y: screen.minY + gap, width: tileW, height: tileH))

// screen glass sheen
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.06))
ctx.fill(CGRect(x: screen.minX, y: screen.midY, width: screen.width, height: screen.height/2))

// ---- write PNG ----------------------------------------------------------
guard let img = ctx.makeImage() else { fatalError("no image") }
let rep = NSBitmapImageRep(cgImage: img)
guard let data = rep.representation(using: .png, properties: [:]) else { fatalError("no png") }
try! data.write(to: URL(fileURLWithPath: outPath))
print("wrote \(outPath)")
