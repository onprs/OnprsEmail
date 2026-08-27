#!/usr/bin/env bash
# ==============================================================================
# Onprs Email 数据一键备份脚本
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

BACKUP_DIR="${SCRIPT_DIR}/backups"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
BACKUP_ARCHIVE="${BACKUP_DIR}/onprs_email_backup_${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

# 先清理过期归档，为 SQLite 快照预留磁盘空间。
find "$BACKUP_DIR" -type f -name "onprs_email_backup_*.tar.gz" -mtime +14 -exec rm -f {} \;

echo "[INFO] 开始执行邮件系统数据备份: ${TIMESTAMP}..."

STALWART_CONFIG_PATH="${STALWART_CONFIG_PATH:-/opt/onprs-email/stalwart-config}"
STALWART_DATA_PATH="${STALWART_DATA_PATH:-/opt/onprs-email/stalwart-data}"
INGRESS_PATH="${INGRESS_DATA_PATH:-/opt/onprs-email/ingress-data}"
SNAPPYMAIL_PATH="${SNAPPYMAIL_DATA_PATH:-/opt/onprs-email/snappymail-data}"

INGRESS_DB_PATH="${INGRESS_PATH}/ingress_emails.db"
INGRESS_SNAPSHOT_DIR="$(mktemp -d "${BACKUP_DIR}/.ingress_snapshot_${TIMESTAMP}_XXXXXX")"
trap 'rm -rf "$INGRESS_SNAPSHOT_DIR"' EXIT
mkdir -p "${INGRESS_SNAPSHOT_DIR}/$(basename "$INGRESS_PATH")"

if [ -f "$INGRESS_DB_PATH" ]; then
    python3 - "$INGRESS_DB_PATH" "${INGRESS_SNAPSHOT_DIR}/$(basename "$INGRESS_PATH")/ingress_emails.db" <<'PY'
import sqlite3
import sys

source_path, snapshot_path = sys.argv[1:]
with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
    with sqlite3.connect(snapshot_path) as snapshot:
        source.backup(snapshot)
PY
fi

tar -czf "$BACKUP_ARCHIVE" \
    -C "$(dirname "$STALWART_CONFIG_PATH")" "$(basename "$STALWART_CONFIG_PATH")" \
    -C "$(dirname "$STALWART_DATA_PATH")" "$(basename "$STALWART_DATA_PATH")" \
    -C "$INGRESS_SNAPSHOT_DIR" "$(basename "$INGRESS_PATH")" \
    -C "$(dirname "$SNAPPYMAIL_PATH")" "$(basename "$SNAPPYMAIL_PATH")"

echo "[SUCCESS] 备份成功！备份文件保存于: ${BACKUP_ARCHIVE}"
echo "[INFO] 文件大小: $(du -sh "$BACKUP_ARCHIVE" | awk '{print $1}')"
echo "[INFO] 已自动清理 14 天前的旧备份文件。"
