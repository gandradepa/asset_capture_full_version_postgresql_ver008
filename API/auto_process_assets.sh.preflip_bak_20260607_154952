#!/bin/bash

# --- CONFIGURATION ---
# Define paths
API_DIR="/home/developer/API"
VENV_PYTHON="/home/developer/asset_capture_app_dev/venv/bin/python"
LOG_DIR="/home/developer/asset_capture_app_dev/logs/automation"

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Define timestamp for logging
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
LOG_FILE="$LOG_DIR/run_$TIMESTAMP.log"

echo "--- Starting Automated Asset Processing: $TIMESTAMP ---" >> "$LOG_FILE"

# --- STEP 1: Run Electrical AI Interpreter ---
echo "[1/3] Running Electrical AI..." >> "$LOG_FILE"
$VENV_PYTHON "$API_DIR/API_interface_EL_ver00.py" >> "$LOG_FILE" 2>&1

# --- STEP 2: Run Mechanical AI Interpreter ---
echo "[2/3] Running Mechanical AI..." >> "$LOG_FILE"
$VENV_PYTHON "$API_DIR/API_interface_ME_ver00.py" >> "$LOG_FILE" 2>&1

# Note: Add Backflow (BF) here if needed in the future

# --- STEP 3: Run Database Synchronization ---
echo "[3/3] Syncing JSON to Database..." >> "$LOG_FILE"
$VENV_PYTHON "$API_DIR/updating_process_database.py" >> "$LOG_FILE" 2>&1

echo "--- Automation Complete ---" >> "$LOG_FILE"
