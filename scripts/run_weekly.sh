#!/bin/bash
# Weekly 2-year history refresh. Scheduled by launchd/com.marketpulse.weekly.plist.
exec "$(cd "$(dirname "$0")" && pwd)/run_job.sh" weekly --refresh-history
