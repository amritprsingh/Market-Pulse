#!/bin/bash
# Unload and remove the MarketPulse launchd jobs. Safe to re-run.
set -eu

AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABELS="com.marketpulse.daily com.marketpulse.weekly"

for label in $LABELS; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$AGENTS/$label.plist"
    echo "Removed $label"
done
