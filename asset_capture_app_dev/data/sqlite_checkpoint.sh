#!/bin/bash
DB_PATH="/home/developer/asset_capture_app_dev/data/QR_codes.db"

if [ -f "$DB_PATH" ]; then
    echo "Iniciando Checkpoint em: $DB_PATH"
    /usr/bin/sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);"
    echo "Sucesso! Arquivo WAL truncado."
else
    echo "Erro: Banco de dados não encontrado em $DB_PATH"
fi
