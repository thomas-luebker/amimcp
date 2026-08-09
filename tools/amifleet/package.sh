#!/bin/bash
# package.sh — build amifleet.app: universal binary, icon, Developer ID sign,
# and (with a notary profile) notarize + staple into a ready-to-ship DMG.
#
#   ./package.sh                 build + sign (Developer ID), verify
#   ./package.sh --notarize <p>  ... then notarize with keychain profile <p>
#                                    and staple; also builds a DMG
#
# Set up the notary profile once (either credential works):
#   xcrun notarytool store-credentials amifleet-notary \
#       --apple-id you@example.com --team-id Y38P2BJ4DM --password <app-specific>
#   # or: --key AuthKey_XXXX.p8 --key-id XXXX --issuer <issuer-uuid>
set -euo pipefail

cd "$(dirname "$0")"
APP="amifleet"
BUNDLE_ID="com.amiga-imager.amifleet"
VERSION="1.0.0"
IDENTITY="Developer ID Application: Thomas Luebker (Y38P2BJ4DM)"
BUILD="build"
APPDIR="$BUILD/$APP.app"

NOTARIZE=""
if [ "${1:-}" = "--notarize" ]; then NOTARIZE="${2:?need a keychain profile name}"; fi

echo "==> Clean"
rm -rf "$BUILD"; mkdir -p "$APPDIR/Contents/MacOS" "$APPDIR/Contents/Resources"

echo "==> Icon (.icns)"
ICONSET="$BUILD/$APP.iconset"; mkdir -p "$ICONSET"
SRC="icon/icon-1024.png"
[ -f "$SRC" ] || swift icon/makeicon.swift "$SRC"
for pair in "16 16x16" "32 16x16@2x" "32 32x32" "64 32x32@2x" \
            "128 128x128" "256 128x128@2x" "256 256x256" "512 256x256@2x" \
            "512 512x512" "1024 512x512@2x"; do
    set -- $pair
    sips -z "$1" "$1" "$SRC" --out "$ICONSET/icon_$2.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APPDIR/Contents/Resources/$APP.icns"

echo "==> Universal binary (arm64 + x86_64)"
swift build -c release --arch arm64 --arch x86_64 --product "$APP" >/dev/null
cp "$(swift build -c release --arch arm64 --arch x86_64 --product "$APP" --show-bin-path)/$APP" \
   "$APPDIR/Contents/MacOS/$APP"
chmod +x "$APPDIR/Contents/MacOS/$APP"

echo "==> Info.plist"
cat > "$APPDIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP</string>
    <key>CFBundleDisplayName</key><string>amifleet</string>
    <key>CFBundleExecutable</key><string>$APP</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleIconFile</key><string>$APP</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSLocalNetworkUsageDescription</key>
    <string>amifleet reaches the amiagent daemons on your Amigas over your local network.</string>
    <key>NSHumanReadableCopyright</key><string>(c) 2026 Thomas Luebker. MIT-licensed.</string>
</dict>
</plist>
PLIST

echo "==> Sign (Developer ID, hardened runtime, secure timestamp)"
codesign --force --options runtime --timestamp \
    --sign "$IDENTITY" "$APPDIR/Contents/MacOS/$APP"
codesign --force --options runtime --timestamp \
    --sign "$IDENTITY" "$APPDIR"
echo "==> Verify signature"
codesign --verify --deep --strict --verbose=2 "$APPDIR"
spctl -a -vvv --type execute "$APPDIR" 2>&1 || \
    echo "   (spctl rejects until notarized — expected before the notarize step)"

if [ -n "$NOTARIZE" ]; then
    ZIP="$BUILD/$APP.zip"
    echo "==> Notarize via profile '$NOTARIZE'"
    /usr/bin/ditto -c -k --keepParent "$APPDIR" "$ZIP"
    xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARIZE" --wait
    echo "==> Staple"
    xcrun stapler staple "$APPDIR"
    xcrun stapler validate "$APPDIR"
    spctl -a -vvv --type execute "$APPDIR"

    echo "==> DMG"
    DMG="$BUILD/$APP-$VERSION.dmg"
    STAGE="$BUILD/dmg"; rm -rf "$STAGE"; mkdir -p "$STAGE"
    cp -R "$APPDIR" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
    hdiutil create -volname "amifleet" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
    codesign --force --timestamp --sign "$IDENTITY" "$DMG"
    xcrun stapler staple "$DMG" || echo "   (DMG staple optional)"
    echo "==> Shipped: $DMG"
else
    echo "==> Signed app at $APPDIR (run with --notarize <profile> to notarize + DMG)"
fi
