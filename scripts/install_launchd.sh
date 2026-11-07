#!/bin/bash
# Install and load the MarketPulse launchd jobs. Safe to re-run.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABELS="com.marketpulse.daily com.marketpulse.weekly"

mkdir -p "$AGENTS" "$REPO/logs"

for label in $LABELS; do
    plist="$REPO/launchd/$label.plist"
    # Plists can't use relative paths, so a moved repo must fail here, not at 18:30.
    if ! grep -q "<string>$REPO/scripts/" "$plist"; then
        echo "$plist does not point at $REPO; update its paths first" >&2
        exit 1
    fi
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    cp "$plist" "$AGENTS/"
    launchctl bootstrap "$DOMAIN" "$AGENTS/$label.plist"
    echo "Loaded $label"
done
