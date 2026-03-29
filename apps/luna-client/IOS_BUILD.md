# Luna iOS Build Guide

## Prerequisites (on your Mac)

```bash
# Xcode 15+ from App Store
# Xcode CLI tools
xcode-select --install

# Rust iOS targets
rustup target add aarch64-apple-ios x86_64-apple-ios aarch64-apple-ios-sim

# Tauri CLI
cargo install tauri-cli --version "^2"
# or: npm install -g @tauri-apps/cli@next
```

## 1. Set your Development Team

In `src-tauri/tauri.conf.json`, set your Apple Developer Team ID:
```json
"iOS": {
  "minimumSystemVersion": "16.0",
  "developmentTeam": "YOUR_TEAM_ID"
}
```
Find your Team ID at https://developer.apple.com/account → Membership.

## 2. Initialize the Xcode project (first time only)

```bash
cd apps/luna-client
npm install
cargo tauri ios init
```

This generates `src-tauri/gen/apple/` — the Xcode project. Commit it.

## 3. Run on Simulator

```bash
cargo tauri ios dev
# or target a specific simulator:
cargo tauri ios dev --target aarch64-apple-ios-sim
```

## 4. Run on Device

Connect your iPhone, trust the Mac, then:
```bash
cargo tauri ios dev --device
```

## 5. Build for release (TestFlight / App Store)

```bash
cargo tauri ios build
# Output: src-tauri/gen/apple/build/arm64/Luna.ipa
```

Upload via Xcode Organizer or `xcrun altool`.

## iOS-specific features

| Feature | Status |
|---------|--------|
| Haptic feedback | ✅ `tauri-plugin-haptics` |
| Push notifications | ✅ `tauri-plugin-notification` |
| Screenshot | ❌ Use system share sheet (iOS sandbox restriction) |
| Global shortcuts | ❌ Desktop only |
| System tray | ❌ Desktop only |
| Auto-updater | ❌ Use App Store / TestFlight |

## App Store entitlements

Add to `src-tauri/gen/apple/Luna_iOS/Luna_iOS.entitlements` as needed:
- `com.apple.developer.push-notifications` — for push notifications
- `com.apple.security.network.client` — for outbound network (usually auto-added)
