#!/bin/bash
# Daily catch-up run. Scheduled by launchd/com.marketpulse.daily.plist.
exec "$(cd "$(dirname "$0")" && pwd)/run_job.sh" daily
