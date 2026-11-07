#!/bin/bash
# Shared runner for scheduled jobs: appends to logs/<job>.log and takes one lock,
# shared by all jobs, so two runs never overlap.
# Usage: run_job.sh <job name> [etl.run_daily arguments]
set -u

JOB="$1"
shift

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG="$REPO/logs/$JOB.log"
LOCK="$REPO/logs/.run.lock"

# A normal run takes seconds. Waiting, not skipping, matters when daily and weekly
# both fire as the Mac wakes: a skipped weekly refresh would be lost for a week.
LOCK_WAIT_SECONDS="${LOCK_WAIT_SECONDS:-600}"
LOCK_MAX_AGE_SECONDS="${LOCK_MAX_AGE_SECONDS:-7200}"
LOCK_POLL_SECONDS="${LOCK_POLL_SECONDS:-5}"
LOCK_SETUP_SECONDS=60

cd "$REPO" || exit 1
mkdir -p logs
exec >> "$LOG" 2>&1

stamp() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $JOB: $*"
}

lock_age() {
    local modified
    modified="$(stat -f %m "$LOCK" 2>/dev/null)" || modified="$(date +%s)"
    echo $(($(date +%s) - modified))
}

# Checks the command too, because the PID may have been reused after a crash.
holder_is_running() {
    [ -n "$1" ] && ps -p "$1" -o command= 2>/dev/null | grep -q 'run_job\.sh'
}

clear_stale_lock() {
    local pid age
    pid="$(cat "$LOCK/pid" 2>/dev/null)"
    age="$(lock_age)"
    if holder_is_running "$pid"; then
        [ "$age" -lt "$LOCK_MAX_AGE_SECONDS" ] && return
        stamp "stopping hung run $pid (lock age ${age}s)"
        pkill -TERM -P "$pid"
        kill -TERM "$pid"
    elif [ -z "$pid" ] && [ "$age" -lt "$LOCK_SETUP_SECONDS" ]; then
        return # Another run has just created the lock and is writing its PID.
    fi
    stamp "removing stale lock (pid ${pid:-unknown}, age ${age}s)"
    mv "$LOCK" "$LOCK.stale.$$" 2>/dev/null && rm -rf "$LOCK.stale.$$"
}

acquire_lock() {
    local deadline=$(($(date +%s) + LOCK_WAIT_SECONDS))
    until mkdir "$LOCK" 2>/dev/null; do
        clear_stale_lock
        [ "$(date +%s)" -ge "$deadline" ] && return 1
        sleep "$LOCK_POLL_SECONDS"
    done
    echo $$ > "$LOCK/pid"
}

# Only the holder releases, so a run stopped as hung can't remove its successor's lock.
release_lock() {
    [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"
}

trap release_lock EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

stamp "started"
if ! acquire_lock; then
    stamp "skipped: lock still held after ${LOCK_WAIT_SECONDS}s"
    exit 75
fi

"$PYTHON" -m etl.run_daily "$@"
status=$?
stamp "finished with exit code $status"
exit "$status"
