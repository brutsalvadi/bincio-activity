#!/usr/bin/env bash
# Bincio mobile app — one-time setup
# Run from the mobile/ directory: ./setup.sh
# Or from the repo root:           bash mobile/setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET} $*"; }
die()  { echo -e "${RED}✗${RESET} $*" >&2; exit 1; }
step() { echo -e "\n${YELLOW}▸${RESET} $*"; }

echo ""
echo "  Bincio mobile setup"
echo "  ═══════════════════"
echo ""

# ── 1. Node.js ────────────────────────────────────────────────────────────────
step "Checking Node.js..."
if ! command -v node &>/dev/null; then
  die "Node.js not found. Install from https://nodejs.org (v20+ recommended)."
fi
NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
  die "Node.js 18+ required (found $(node -v)). Update at https://nodejs.org"
fi
ok "Node.js $(node -v)"

# ── 2. npm ────────────────────────────────────────────────────────────────────
if ! command -v npm &>/dev/null; then
  die "npm not found. It ships with Node.js — check your installation."
fi
ok "npm $(npm -v)"

# ── 3. Expo CLI (global, optional — we use npx) ───────────────────────────────
step "Checking Expo CLI..."
if command -v expo &>/dev/null; then
  ok "Expo CLI $(expo --version) (global)"
else
  warn "Expo CLI not installed globally. Using npx instead (slightly slower)."
  warn "Install globally with: npm install -g expo-cli"
fi

# ── 4. Platform tools ─────────────────────────────────────────────────────────
step "Checking platform tools..."
PLATFORM="$(uname -s)"

if [ "$PLATFORM" = "Darwin" ]; then
  if command -v xcodebuild &>/dev/null; then
    ok "Xcode $(xcodebuild -version 2>/dev/null | head -1 | awk '{print $2}')"
  else
    warn "Xcode not found — iOS builds will not work."
    warn "Install Xcode from the App Store, then: xcode-select --install"
  fi
  if command -v xcrun &>/dev/null && xcrun --sdk iphoneos --show-sdk-version &>/dev/null; then
    ok "iOS SDK available"
  fi
fi

if command -v adb &>/dev/null; then
  ok "Android SDK / adb found"
else
  warn "adb not found — Android builds require Android Studio."
  warn "Install from https://developer.android.com/studio"
fi

# ── 5. Install dependencies ───────────────────────────────────────────────────
step "Installing npm dependencies..."
if [ -d node_modules ] && [ -f node_modules/.package-lock.json ]; then
  ok "node_modules already present — running npm install to sync..."
fi
npm install
ok "Dependencies installed"

# ── 6. expo-env.d.ts (required by expo-router) ────────────────────────────────
step "Generating Expo type declarations..."
npx expo customize expo-env.d.ts --no-install 2>/dev/null || true
if [ ! -f expo-env.d.ts ]; then
  echo '/// <reference types="expo-router/types" />' > expo-env.d.ts
fi
ok "expo-env.d.ts ready"

# ── 7. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "  ══════════════════════════════════════════"
echo "   Setup complete! Next steps:"
echo ""
echo "   Start with Expo Go (scan QR on your phone):"
echo "     npx expo start"
echo ""
echo "   Run on Android emulator:"
echo "     npx expo run:android"
echo ""
echo "   Run on iOS simulator (macOS only):"
echo "     npx expo run:ios"
echo ""
echo "   Build APK for Karoo sideload:"
echo "     npx eas build -p android --profile preview"
echo "  ══════════════════════════════════════════"
echo ""
