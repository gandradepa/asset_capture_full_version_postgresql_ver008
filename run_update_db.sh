#!/bin/bash

# Ativar a virtualenv correta
source /home/developer/API/.venv/bin/activate

# Idempotent migration: ensure the unified audit_trail table exists.
python /home/developer/scripts/migrate_create_audit_trail.py

# Executar o script Python
python /home/developer/API/updating_process_database.py
