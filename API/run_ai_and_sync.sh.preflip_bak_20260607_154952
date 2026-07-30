#!/bin/bash

# Force unbuffered output so logs appear immediately
export PYTHONUNBUFFERED=1

# 1. Define Paths
VENV_ACTIVATE="/home/developer/asset_capture_app_dev/venv/bin/activate"
DB_UPDATE_SCRIPT="/home/developer/API/updating_process_database.py"

# 2. Activate Virtual Environment
source "$VENV_ACTIVATE"

# 3. Run the AI Script (Passed as the first argument)
AI_SCRIPT="$1"
echo "=================================================="
echo "--- [STEP 1/2] Running AI Interpreter: $(basename "$AI_SCRIPT") ---"
echo "=================================================="

# Run Python and ensure output goes to stdout (which the dashboard captures)
python3 "$AI_SCRIPT"
AI_EXIT_CODE=$?

# 4. If AI succeeded, Run Database Sync
if [ $AI_EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=================================================="
    echo "--- [STEP 2/2] AI Successful. Triggering Database Sync... ---"
    echo "=================================================="
    python3 "$DB_UPDATE_SCRIPT"
else
    echo ""
    echo "❌ AI Interpreter failed. Skipping database sync."
    exit 1
fi
