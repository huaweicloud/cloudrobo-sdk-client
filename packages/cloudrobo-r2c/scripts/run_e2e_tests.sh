#!/bin/bash
# Run R2C SDK end-to-end tests covering all execution paths.
# Requires a Zenoh router running on tcp/127.0.0.1:7447.
# Usage: bash scripts/run_e2e_tests.sh [--chunk-size 100] [--duration 8]

set -euo pipefail

CHUNK_SIZE=100
TEST_DURATION=6
while [[ $# -gt 0 ]]; do
    case "$1" in
        --chunk-size) CHUNK_SIZE="$2"; shift 2 ;;
        --duration) TEST_DURATION="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

PROJECT_DIR="/home/robot/suhanwu/r2c_sdk_python"
cd "$PROJECT_DIR"

# Example configs shipped inside the package (see pyproject.toml package-data).
CONFIG_DIR="src/cloudrobo_r2c/config"

TMPDIR="/tmp/r2c-e2e-$$"
mkdir -p "$TMPDIR"
PASS=0; FAIL=0; TOTAL=0

cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    pkill -f "mock_policy_server" 2>/dev/null || true
    pkill -f "cloudroboclient" 2>/dev/null || true
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

run_test() {
    local name="$1"; shift
    local config="$1"; shift
    TOTAL=$((TOTAL + 1))

    echo ""
    echo "=== TEST: $name ==="
    echo "    config: $config"

    local log="$TMPDIR/$name.log"
    local timeout=$((TEST_DURATION + 10))

    uv run python -m r2c_sdk.cloudroboclient \
        --robot-config "$config" \
        --client-config "$CONFIG_DIR/client_config.yaml" \
        --duration "$TEST_DURATION" \
        --log-level INFO > "$log" 2>&1 &
    local client_pid=$!

    local waited=0
    while kill -0 "$client_pid" 2>/dev/null && [ "$waited" -lt "$timeout" ]; do
        sleep 0.5; waited=$((waited + 1))
    done
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true

    local checks=0
    grep -q "Zenoh connectivity listener started"    "$log" && checks=$((checks + 1))
    grep -q "SyncRobotClient started"               "$log" && checks=$((checks + 1))
    grep -q "message sent(observation)"              "$log" && checks=$((checks + 1))
    grep -q "SyncRobotClient stopped"                "$log" && checks=$((checks + 1))

    if [ "$checks" -ge 4 ]; then
        echo "    ✅ PASS ($checks/4 checks)"
        grep -E "Zenoh|SyncRobotClient started|message sent|SyncRobotClient stopped" "$log" | sed 's/^/    | /'
        PASS=$((PASS + 1))
    else
        echo "    ❌ FAIL ($checks/4 checks)"
        grep -E "ERROR|Zenoh|SyncRobotClient|message sent" "$log" | sed 's/^/    | /'
        FAIL=$((FAIL + 1))
    fi
}

# ── main ─────────────────────────────────────────────────────────────────────

echo "============================================"
echo " R2C SDK E2E Test Suite (via Zenoh Router)"
echo " chunk_size=$CHUNK_SIZE  duration=${TEST_DURATION}s"
echo "============================================"

# Start mock policy server in client mode (connects to Zenoh Router)
echo ""
echo "[MOCK] Starting mock policy server in client mode (chunk_size=$CHUNK_SIZE)..."
uv run python scripts/mock_policy_server.py \
    --client-config "$CONFIG_DIR/client_config.yaml" \
    --chunk-size "$CHUNK_SIZE" \
    --delay-ms 30 \
    --log-level WARNING > "$TMPDIR/mock_policy.log" 2>&1 &
MOCK_PID=$!
sleep 2
if kill -0 "$MOCK_PID" 2>/dev/null; then
    echo "[MOCK] Ready (PID=$MOCK_PID, client mode via Router)"
else
    echo "[MOCK] FAILED to start"
    cat "$TMPDIR/mock_policy.log"
    exit 1
fi

# Run all test variants
run_test "sync_default"           "$CONFIG_DIR/dummy/robot_dummy_sync.yaml"
run_test "async_threshold0"       "$CONFIG_DIR/dummy/robot_dummy_async_threshold0.yaml"
run_test "async_replace"          "$CONFIG_DIR/dummy/robot_dummy_async.yaml"
run_test "async_fusion"           "$CONFIG_DIR/dummy/robot_dummy_async_fusion.yaml"
run_test "dry_run"                "$CONFIG_DIR/dummy/robot_dummy_dry_run.yaml"
run_test "chunk_alignment"        "$CONFIG_DIR/dummy/robot_dummy_alignment.yaml"
run_test "max_enqueue"            "$CONFIG_DIR/dummy/robot_dummy_max_enqueue.yaml"

# ── report ───────────────────────────────────────────────────────────────────

kill "$MOCK_PID" 2>/dev/null || true
wait "$MOCK_PID" 2>/dev/null || true

echo ""
echo "============================================"
echo " RESULTS: $PASS/$TOTAL passed"
echo "============================================"
if [ "$FAIL" -gt 0 ]; then
    echo "FAILURES:"
    for f in "$TMPDIR"/*.log; do
        name=$(basename "$f" .log)
        if ! grep -q "SyncRobotClient stopped" "$f"; then
            echo "  $name"
        fi
    done
fi
echo ""
echo "Mock server activity:"
grep -c "Received" "$TMPDIR/mock_policy.log" 2>/dev/null && echo "  observations received" || echo "  0 observations"
grep -c "Published" "$TMPDIR/mock_policy.log" 2>/dev/null && echo "  actions published" || echo "  0 actions"
exit $FAIL
