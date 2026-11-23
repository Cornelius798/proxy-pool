#!/bin/bash
# Run one subs-check round in the background, wait for it to finish,
# then kill the daemon.
#
# subs-check has no one-shot mode: it runs one check immediately, then
# loops on check-interval (10080 min in our configs, so no second
# round starts during the workflow). The workflow therefore starts it
# in the background, waits for the round to complete, and stops it.
#
# Round completion is detected from the log, not from the process
# exiting: the pipeline logs "可用节点数量" when the check finishes,
# then SaveConfig writes <output-dir>/all.yaml (or logs
# "本轮没有可保存的节点" when the round yields zero nodes).
#
# Usage: run_subs_check.sh <config.yaml> <output-dir> <log-file> [timeout-min]
# Exit 0 when the round completed (all.yaml saved, or the round
# legitimately yielded zero nodes). Exit 1 on timeout or early exit.
set -u

CONFIG="$1"
OUTDIR="$2"
LOG="$3"
TIMEOUT_MIN="${4:-45}"

.bin/subs-check -f "$CONFIG" > "$LOG" 2>&1 &
PID=$!
echo "subs-check started (pid $PID) with $CONFIG"

DEADLINE=$((SECONDS + TIMEOUT_MIN * 60))
ROUND_DONE=0
while [ "$SECONDS" -lt "$DEADLINE" ]; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "ERROR: subs-check exited early"
        tail -5 "$LOG"
        exit 1
    fi
    # Empty round: subs-check logs this and saves nothing.
    if grep -q "本轮没有可保存的节点" "$LOG"; then
        echo "round done: zero nodes"
        ROUND_DONE=1
        break
    fi
    # Round pipeline done -> save follows; wait for all.yaml to land.
    if grep -q "可用节点数量" "$LOG"; then
        for _ in $(seq 1 24); do
            if [ -s "$OUTDIR/all.yaml" ]; then
                echo "round done: $OUTDIR/all.yaml saved"
                ROUND_DONE=1
                break
            fi
            if grep -q "本轮没有可保存的节点" "$LOG"; then
                echo "round done: zero nodes"
                ROUND_DONE=1
                break
            fi
            sleep 5
        done
        [ "$ROUND_DONE" = 1 ] && break
    fi
    sleep 10
done

if [ "$ROUND_DONE" != 1 ]; then
    echo "ERROR: subs-check round did not finish within ${TIMEOUT_MIN} min"
    tail -5 "$LOG"
    kill "$PID" 2>/dev/null || true
    exit 1
fi

# Let the save flush, then stop the daemon (it would otherwise wait
# for the next check-interval round).
sleep 3
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
echo "subs-check stopped"
