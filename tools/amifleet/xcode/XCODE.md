# Shipping amifleet from Xcode (Archive → Notarize)

amifleet is a Swift Package. Xcode's Organizer can't Archive a SwiftPM
*executable* into a notarizable `.app`, so to notarise through the Xcode GUI you
wrap the same code in a thin **macOS App project** that links our `AmigaKit`
package. It's ~10 minutes of clicking, once. After that it's Archive →
Distribute → done.

Everything you need is already here:
- the source lives in `tools/amifleet/Sources/` (unchanged — you reference it),
- the icon is `tools/amifleet/xcode/Assets.xcassets/AppIcon.appiconset`.

---

## 1. New macOS App project

**File → New → Project… → macOS → App.**

- Product Name: **amifleet**
- Team: **Thomas Luebker (Y38P2BJ4DM)**
- Organization Identifier: **com.amiga-imager**  → bundle id becomes
  `com.amiga-imager.amifleet` (matches the CLI build).
- Interface: **SwiftUI**, Language: **Swift**.
- Save it anywhere, e.g. `tools/amifleet/xcode/` (it's gitignored there).

## 2. Remove the template's entry point

The template adds `amifleetApp.swift` (with `@main`) and `ContentView.swift`.
Our `App.swift` already has `@main`, so **delete both generated files** (Move to
Trash) or you'll get "duplicate @main".

## 3. Add our source

Drag these five files from `tools/amifleet/Sources/amifleet/` into the project
(check **"Copy items if needed"** off — reference them so edits stay in git):

```
App.swift  FleetView.swift  DetailView.swift  ScreenView.swift  Models.swift
```

They already `import AmigaKit`; that resolves in the next step.

## 4. Add the AmigaKit package

**File → Add Package Dependencies… → Add Local…** → choose the
`tools/amifleet` folder (the one with `Package.swift`). Add the **AmigaKit**
library product to the **amifleet** app target. (Ignore the `amifleet`/`amitest`
executable products — you only want the `AmigaKit` library.)

## 5. Icon

In the project's **Assets.xcassets**, delete the empty **AppIcon**, then drag in
`tools/amifleet/xcode/Assets.xcassets/AppIcon.appiconset`. (Or drag the whole
`Assets.xcassets` in and point **Build Settings → Asset Catalog App Icon Set
Name** at `AppIcon`.)

## 6. Info.plist keys

In the target's **Info** tab, add:
- **Application Category** (optional): Utilities
- **Privacy - Local Network Usage Description** →
  `amifleet reaches the amiagent daemons on your Amigas over your local network.`
- Minimum Deployments is set on the target's **General** tab → **macOS 13.0**.

## 7. Signing & capabilities

**Signing & Capabilities** tab:
- **Automatically manage signing** on, Team = your team. (For the release build
  Xcode uses a Developer ID automatically when you pick Developer ID
  distribution in step 9.)
- **Hardened Runtime** is added automatically for Developer ID distribution.
- Leave **App Sandbox OFF** — a non-sandboxed Developer ID app makes outbound
  network connections with no extra entitlement. *If* you add the sandbox, you
  must also add **Network → Outgoing Connections (Client)**, or it can't reach
  the Amigas.

## 8. Build & run

Set the run destination to **My Mac**, press **Run**. You should get the fleet
board with its icon in the Dock. Fix any file-reference issues here, not after
archiving.

## 9. Archive & notarise

1. Set the scheme to **Release**: **Product → Scheme → Edit Scheme → Run →
   Build Configuration → Release** (Archive uses Release regardless, but this
   makes local runs match).
2. **Product → Archive.** The Organizer opens.
3. Select the archive → **Distribute App → Direct Distribution** (Developer ID).
   Xcode signs, **uploads to Apple's notary service, waits, and staples the
   ticket for you.**
4. When it finishes, **Export** the app. That exported `amifleet.app` is
   notarised and ready — zip it or wrap it in a DMG for users.

Verify from Terminal if you like:
```
spctl -a -vvv --type execute /path/to/amifleet.app     # → accepted, Notarized Developer ID
xcrun stapler validate /path/to/amifleet.app           # → The validate action worked!
```

---

### Why not just `package.sh`?

`../package.sh --notarize <profile>` does all of steps 5–9 headlessly (icon,
universal binary, sign, notarise, staple, DMG) and is what CI would use. The
Xcode route above is for when you'd rather click through Apple's GUI and let the
Organizer manage the upload. Both produce the same notarised app; use whichever
you prefer.
