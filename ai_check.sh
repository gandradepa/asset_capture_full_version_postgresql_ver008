#!/bin/bash

# --- Configuration ---
# Database path from your manual
DB_PATH="/home/developer/asset_capture_app_dev/data/QR_codes.db"

# Log file location
LOG_FILE="/home/developer/ai_check.log"
LOG_RETENTION_DAYS=5

# Python Executable 
# derived from: /home/developer/asset_capture_app_dev + /venv/
PYTHON_EXEC="/home/developer/asset_capture_app_dev/venv/bin/python"

# Directory where Python files are deployed
API_DIR="/home/developer/API"
SCRIPT_VERSION="2026-02-24-debug-02"
SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"
LOCK_STATE_FILE="/tmp/ai_check.lock.state"

describe_lock_holder() {
    local holder_pid=""
    local holder_start=""
    local now=""
    local age=""
    if [ -f "$LOCK_STATE_FILE" ]; then
        # format: <pid> <start_epoch> <version>
        read -r holder_pid holder_start _ < "$LOCK_STATE_FILE" || true
        if [ -n "$holder_pid" ]; then
            if kill -0 "$holder_pid" 2>/dev/null; then
                now=$(date +%s)
                if [[ "$holder_start" =~ ^[0-9]+$ ]]; then
                    age=$((now - holder_start))
                    echo "holder_pid=$holder_pid holder_age=${age}s"
                else
                    echo "holder_pid=$holder_pid holder_age=unknown"
                fi
            else
                echo "holder_pid=$holder_pid holder_status=not_running"
            fi
            return
        fi
    fi
    echo "holder_pid=unknown"
}

log_lock_holder_snapshot() {
    local holder_pid=""
    local ps_line=""
    if [ -f "$LOCK_STATE_FILE" ]; then
        read -r holder_pid _ _ < "$LOCK_STATE_FILE" || true
    fi
    if [ -n "$holder_pid" ] && kill -0 "$holder_pid" 2>/dev/null; then
        ps_line=$(ps -p "$holder_pid" -o pid=,etime=,%cpu=,%mem=,cmd= 2>/dev/null | sed 's/^ *//')
        if [ -n "$ps_line" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: Lock holder snapshot: $ps_line" >> "$LOG_FILE"
        fi
    fi
}

# Single-run lock (prevents overlapping cron executions from clobbering the live log file)
LOCK_FILE="/tmp/ai_check.lock"
LAST_LOG_FLAG="/tmp/ai_check_last_skipped"

if command -v flock >/dev/null 2>&1; then
    # Secure flock. Do not use lock age to kill running jobs; long ME runs are valid.
    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        # Rate limit the log spam: Only write the "skipped" message once every 5 minutes
        if [ ! -f "$LAST_LOG_FLAG" ] || [ -n "$(find "$LAST_LOG_FLAG" -mmin +5 2>/dev/null)" ]; then
            holder_info=$(describe_lock_holder)
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: Previous ai_check run still active. Skipping this cycle. $holder_info" >> "$LOG_FILE"
            log_lock_holder_snapshot
            touch "$LAST_LOG_FLAG"
        fi
        exit 0
    fi
    echo "$$ $(date +%s) $SCRIPT_VERSION" > "$LOCK_STATE_FILE"
    trap 'rm -f "$LOCK_STATE_FILE" >/dev/null 2>&1 || true' EXIT
else
    # Fallback lock for environments without flock
    LOCK_DIR="${LOCK_FILE}.dir"
    LOCK_PID_FILE="$LOCK_DIR/pid"

    # Fallback stale-lock cleanup using PID liveness (no force-kill).
    if [ -d "$LOCK_DIR" ]; then
        lock_pid=""
        if [ -f "$LOCK_PID_FILE" ]; then
            lock_pid=$(cat "$LOCK_PID_FILE" 2>/dev/null)
        fi
        if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
            if [ ! -f "$LAST_LOG_FLAG" ] || [ -n "$(find "$LAST_LOG_FLAG" -mmin +5 2>/dev/null)" ]; then
                holder_info=$(describe_lock_holder)
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: Previous ai_check run still active (mkdir lock). Skipping this cycle. $holder_info" >> "$LOG_FILE"
                log_lock_holder_snapshot
                touch "$LAST_LOG_FLAG"
            fi
            exit 0
        fi
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: Removing stale fallback lock directory with no live owner PID." >> "$LOG_FILE"
        rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true
    fi

    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        if [ ! -f "$LAST_LOG_FLAG" ] || [ -n "$(find "$LAST_LOG_FLAG" -mmin +5 2>/dev/null)" ]; then
            holder_info=$(describe_lock_holder)
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: Previous ai_check run still active (mkdir lock). Skipping this cycle. $holder_info" >> "$LOG_FILE"
            log_lock_holder_snapshot
            touch "$LAST_LOG_FLAG"
        fi
        exit 0
    fi
    echo "$$" > "$LOCK_PID_FILE"
    echo "$$ $(date +%s) $SCRIPT_VERSION" > "$LOCK_STATE_FILE"
    trap 'rm -f "$LOCK_STATE_FILE" >/dev/null 2>&1 || true; rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT
fi

# --- Routine ---

echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: ai_check version=$SCRIPT_VERSION path=$SCRIPT_PATH pid=$$ host=$(hostname)" >> "$LOG_FILE"

# Keep only log entries from the last N days (grouped by timestamped blocks).
if [ -f "$LOG_FILE" ]; then
    CUTOFF_EPOCH=$(date -d "$LOG_RETENTION_DAYS days ago" +%s)
    TMP_LOG=$(mktemp)
    awk -v cutoff="$CUTOFF_EPOCH" '
        function to_epoch(date_str,   cmd, epoch) {
            cmd = "date -d \"" date_str "\" +%s"
            cmd | getline epoch
            close(cmd)
            return epoch
        }
        /^\[[0-9]{4}-[0-9]{2}-[0-9]{2} / {
            ts = substr($0, 2, 19)
            keep = (to_epoch(ts) >= cutoff)
        }
        keep { print }
    ' "$LOG_FILE" > "$TMP_LOG" && mv "$TMP_LOG" "$LOG_FILE"
fi

# 1. Check for records with ai_status = '0'
# This requires sqlite3 to be installed (sudo apt install sqlite3)
ROW_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM QR_codes WHERE ai_status = '0';")

# 2. Condition: Run only if count > 0
if [ "$ROW_COUNT" -gt 0 ]; then

    CURRENT_TIME=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: Found $ROW_COUNT pending items. Starting AI scripts in $API_DIR..." >> "$LOG_FILE"

    # Check for script existence in API_DIR
    for required_script in "API_interface_EL_ver00.py" "API_interface_BF_ver00.py" "API_interface_ME_ver00.py"; do
        if [ ! -f "$API_DIR/$required_script" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Missing script: $API_DIR/$required_script" >> "$LOG_FILE"
            exit 1
        fi
    done

    # 3. Execute the Python Scripts using the dedicated venv
    # We also append their output to the log for debugging
    
    # Force Python to flush stdout/stderr immediately to avoid log mixing
    export PYTHONUNBUFFERED=1
    # Emit Python-level fault tracebacks on native crashes (e.g., segfault exit 139).
    export PYTHONFAULTHANDLER=1

    run_script_with_status() {
        local label="${1:-UNKNOWN}"
        local script_path="${2:-}"
        local start_ts
        local end_ts
        local elapsed
        local exit_code
        local script_hash="unavailable"

        if [ -z "$script_path" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Internal wrapper error: empty script path for label '$label'." >> "$LOG_FILE"
            return 2
        fi
        if [ ! -f "$script_path" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Script not found for label '$label': $script_path" >> "$LOG_FILE"
            return 2
        fi

        echo "" >> "$LOG_FILE"
        echo "==================================================" >> "$LOG_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: --- START Output for $label ---" >> "$LOG_FILE"
        if command -v sha1sum >/dev/null 2>&1; then
            script_hash=$(sha1sum "$script_path" | awk '{print substr($1,1,12)}')
        elif command -v shasum >/dev/null 2>&1; then
            script_hash=$(shasum -a 1 "$script_path" | awk '{print substr($1,1,12)}')
        fi
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $label script path: $script_path (sha1=$script_hash)" >> "$LOG_FILE"
        echo "==================================================" >> "$LOG_FILE"

        start_ts=$(date +%s)
        case "$label" in
            EL)
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: EL env: EL_MAX_WORKERS=${EL_MAX_WORKERS:-1}, EL_OCR_MODE=${EL_OCR_MODE:-light}, EL_HYBRID_OCR_AGENT=${EL_HYBRID_OCR_AGENT:-0}, EL_API_MAX_RETRIES=${EL_API_MAX_RETRIES:-1}, EL_API_RETRY_DELAY=${EL_API_RETRY_DELAY:-0.5}, EL_API_TIMEOUT=${EL_API_TIMEOUT:-45}" >> "$LOG_FILE"
                EL_MAX_WORKERS="${EL_MAX_WORKERS:-1}" \
                EL_OCR_MODE="${EL_OCR_MODE:-light}" \
                EL_HYBRID_OCR_AGENT="${EL_HYBRID_OCR_AGENT:-0}" \
                EL_API_MAX_RETRIES="${EL_API_MAX_RETRIES:-1}" \
                EL_API_TIMEOUT="${EL_API_TIMEOUT:-45}" \
                EL_API_RETRY_DELAY="${EL_API_RETRY_DELAY:-0.5}" \
                OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
                $PYTHON_EXEC "$script_path" >> "$LOG_FILE" 2>&1
                ;;
            BF)
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: BF env: BF_MAX_WORKERS=${BF_MAX_WORKERS:-1}, BF_OCR_MODE=${BF_OCR_MODE:-light}, BF_HYBRID_OCR_AGENT=${BF_HYBRID_OCR_AGENT:-0}, BF_API_MAX_RETRIES=${BF_API_MAX_RETRIES:-1}, BF_API_RETRY_DELAY=${BF_API_RETRY_DELAY:-0.5}, BF_API_TIMEOUT=${BF_API_TIMEOUT:-45}" >> "$LOG_FILE"
                BF_MAX_WORKERS="${BF_MAX_WORKERS:-1}" \
                BF_OCR_MODE="${BF_OCR_MODE:-light}" \
                BF_HYBRID_OCR_AGENT="${BF_HYBRID_OCR_AGENT:-0}" \
                BF_API_MAX_RETRIES="${BF_API_MAX_RETRIES:-1}" \
                BF_API_TIMEOUT="${BF_API_TIMEOUT:-45}" \
                BF_API_RETRY_DELAY="${BF_API_RETRY_DELAY:-0.5}" \
                OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
                $PYTHON_EXEC "$script_path" >> "$LOG_FILE" 2>&1
                ;;
            ME)
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: ME env: ME_MAX_WORKERS=${ME_MAX_WORKERS:-1}, ME_OCR_MODE=${ME_OCR_MODE:-light}, ME_HYBRID_OCR_AGENT=${ME_HYBRID_OCR_AGENT:-0}, ME_SIMPLE_MODE=${ME_SIMPLE_MODE:-1}, ME_SIMPLE_MAX_IMAGES=${ME_SIMPLE_MAX_IMAGES:-3}, ME_SIMPLE_MAX_TOKENS=${ME_SIMPLE_MAX_TOKENS:-550}, ME_IMAGE_DETAIL=${ME_IMAGE_DETAIL:-low}, ME_UBC_CONSENSUS_ENABLED=${ME_UBC_CONSENSUS_ENABLED:-1}, ME_UBC_JUDGE_MODEL=${ME_UBC_JUDGE_MODEL:-gpt-5.6-terra}, ME_UBC_JUDGE_DETAIL=${ME_UBC_JUDGE_DETAIL:-original}, ME_UBC_JUDGE_REASONING_EFFORT=${ME_UBC_JUDGE_REASONING_EFFORT:-low}, ME_API_MAX_RETRIES=${ME_API_MAX_RETRIES:-1}, ME_API_RETRY_DELAY=${ME_API_RETRY_DELAY:-0.5}, ME_API_TIMEOUT=${ME_API_TIMEOUT:-45}" >> "$LOG_FILE"
                ME_MAX_WORKERS="${ME_MAX_WORKERS:-1}" \
                ME_OCR_MODE="${ME_OCR_MODE:-light}" \
                ME_HYBRID_OCR_AGENT="${ME_HYBRID_OCR_AGENT:-0}" \
                ME_SIMPLE_MODE="${ME_SIMPLE_MODE:-1}" \
                ME_SIMPLE_MAX_IMAGES="${ME_SIMPLE_MAX_IMAGES:-3}" \
                ME_SIMPLE_MAX_TOKENS="${ME_SIMPLE_MAX_TOKENS:-550}" \
                ME_IMAGE_DETAIL="${ME_IMAGE_DETAIL:-low}" \
                ME_UBC_CONSENSUS_ENABLED="${ME_UBC_CONSENSUS_ENABLED:-1}" \
                ME_UBC_JUDGE_MODEL="${ME_UBC_JUDGE_MODEL:-gpt-5.6-terra}" \
                ME_UBC_JUDGE_DETAIL="${ME_UBC_JUDGE_DETAIL:-original}" \
                ME_UBC_JUDGE_REASONING_EFFORT="${ME_UBC_JUDGE_REASONING_EFFORT:-low}" \
                ME_API_MAX_RETRIES="${ME_API_MAX_RETRIES:-1}" \
                ME_API_TIMEOUT="${ME_API_TIMEOUT:-45}" \
                ME_API_RETRY_DELAY="${ME_API_RETRY_DELAY:-0.5}" \
                OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
                $PYTHON_EXEC "$script_path" >> "$LOG_FILE" 2>&1
                ;;
            *)
                OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
                $PYTHON_EXEC "$script_path" >> "$LOG_FILE" 2>&1
                ;;
        esac
        exit_code=$?
        end_ts=$(date +%s)
        elapsed=$((end_ts - start_ts))

        if [ "$exit_code" -eq 0 ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: --- END Output for $label --- (exit=0, duration=${elapsed}s)" >> "$LOG_FILE"
        else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: --- END Output for $label --- (exit=${exit_code}, duration=${elapsed}s)" >> "$LOG_FILE"
            if [ "$exit_code" -eq 137 ]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $label was killed with exit 137 (likely OOM/SIGKILL)." >> "$LOG_FILE"
            elif [ "$exit_code" -eq 139 ]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $label crashed with exit 139 (SIGSEGV/native crash). Likely from C/C++ dependency (OpenCV/Tesseract/Numpy runtime)." >> "$LOG_FILE"
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Check the last 'ME_ASSET_START'/'ME_ASSET_RESULT' lines for the QR active at crash time." >> "$LOG_FILE"
            fi
        fi
        return "$exit_code"
    }
    
    routine_failed=0
    run_script_with_status "EL" "$API_DIR/API_interface_EL_ver00.py" || routine_failed=1
    run_script_with_status "BF" "$API_DIR/API_interface_BF_ver00.py" || routine_failed=1
    run_script_with_status "ME" "$API_DIR/API_interface_ME_ver00.py" || routine_failed=1

    if [ "$routine_failed" -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: Routine completed." >> "$LOG_FILE"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Routine completed with one or more failed stages." >> "$LOG_FILE"
    fi
    echo "---------------------------------------------------" >> "$LOG_FILE"

else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: No pending ai_status=0 items. Skipping EL/BF/ME runs." >> "$LOG_FILE"
fi
