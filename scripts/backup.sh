#!/usr/bin/env bash
# 创建包含服务文件、Ingress SQLite 快照和 PostgreSQL 导出的备份归档。

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
if [ ! -f "$ENV_FILE" ]; then
    printf '[错误] 找不到环境文件：%s\n' "$ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for command_name in python3 tar sha256sum realpath; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf '[错误] 缺少备份所需命令：%s\n' "$command_name" >&2
        exit 1
    fi
done

BACKUP_DIR="${BACKUP_DIR:-${SCRIPT_DIR}/backups}"
if [[ "$BACKUP_DIR" != /* ]]; then
    printf '[错误] BACKUP_DIR 必须使用绝对路径。\n' >&2
    exit 1
fi
BACKUP_DIR="$(realpath -m -- "$BACKUP_DIR")"
case "$BACKUP_DIR" in
    / | /bin | /boot | /dev | /etc | /home | /lib | /lib64 | /opt | /proc | /root | /run | /sbin | /srv | /sys | /tmp | /usr | /var)
        printf '[错误] BACKUP_DIR 指向危险的系统目录：%s\n' "$BACKUP_DIR" >&2
        exit 1
        ;;
esac
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
if ! [[ "$BACKUP_RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
    printf '[错误] BACKUP_RETENTION_DAYS 必须是非负整数。\n' >&2
    exit 1
fi

STALWART_CONFIG_PATH="$(realpath -m -- "${STALWART_CONFIG_PATH:-/opt/onprs-email/stalwart-config}")"
STALWART_DATA_PATH="$(realpath -m -- "${STALWART_DATA_PATH:-/opt/onprs-email/stalwart-data}")"
INGRESS_DATA_PATH="$(realpath -m -- "${INGRESS_DATA_PATH:-/opt/onprs-email/ingress-data}")"
SNAPPYMAIL_DATA_PATH="$(realpath -m -- "${SNAPPYMAIL_DATA_PATH:-/opt/onprs-email/snappymail-data}")"
INGRESS_DB_PATH="${INGRESS_DATA_PATH}/ingress_emails.db"
POSTGRES_BACKUP_ENABLED="${POSTGRES_BACKUP_ENABLED:-true}"
POSTGRES_BACKUP_CONTAINER="${POSTGRES_BACKUP_CONTAINER:-}"
POSTGRES_BACKUP_DATABASE="${POSTGRES_BACKUP_DATABASE:-}"
POSTGRES_BACKUP_USER="${POSTGRES_BACKUP_USER:-}"

for required_directory in "$STALWART_CONFIG_PATH" "$STALWART_DATA_PATH" "$INGRESS_DATA_PATH" "$SNAPPYMAIL_DATA_PATH"; do
    if [ ! -d "$required_directory" ]; then
        printf '[错误] 备份目录不存在：%s\n' "$required_directory" >&2
        exit 1
    fi
done

STALWART_CONFIG_ROOT="$(basename "$STALWART_CONFIG_PATH")"
STALWART_DATA_ROOT="$(basename "$STALWART_DATA_PATH")"
for archive_root in "$STALWART_CONFIG_ROOT" "$STALWART_DATA_ROOT"; do
    if [[ "$archive_root" = -* ]]; then
        printf '[错误] 备份目录名不能以连字符开头：%s\n' "$archive_root" >&2
        exit 1
    fi
    case "$archive_root" in
        ingress-data | postgresql | metadata | snappymail-data)
            printf '[错误] 归档根目录名称冲突：%s\n' "$archive_root" >&2
            exit 1
            ;;
    esac
done
if [ "$STALWART_CONFIG_ROOT" = "$STALWART_DATA_ROOT" ]; then
    printf '[错误] 两个 Stalwart 备份目录的末级名称必须不同。\n' >&2
    exit 1
fi

mkdir -p -- "$BACKUP_DIR"
chmod 700 -- "$BACKUP_DIR"

TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
ARCHIVE_NAME="onprs_email_backup_${TIMESTAMP}.tar.gz"
BACKUP_ARCHIVE="${BACKUP_DIR}/${ARCHIVE_NAME}"
CHECKSUM_FILE="${BACKUP_ARCHIVE}.sha256"
TEMPORARY_DIR="$(mktemp -d "${BACKUP_DIR}/.backup_${TIMESTAMP}_XXXXXX")"
TEMPORARY_ARCHIVE="${BACKUP_DIR}/.${ARCHIVE_NAME}.tmp"

cleanup() {
    rm -rf -- "$TEMPORARY_DIR"
    rm -f -- "$TEMPORARY_ARCHIVE" "${CHECKSUM_FILE}.tmp"
}
trap cleanup EXIT

if [ -e "$BACKUP_ARCHIVE" ] || [ -e "$CHECKSUM_FILE" ]; then
    printf '[错误] 同名备份已存在：%s\n' "$BACKUP_ARCHIVE" >&2
    exit 1
fi

mkdir -p \
    "$TEMPORARY_DIR/ingress-data" \
    "$TEMPORARY_DIR/postgresql" \
    "$TEMPORARY_DIR/snappymail-data" \
    "$TEMPORARY_DIR/metadata"
printf '[信息] 开始创建备份：%s\n' "$TIMESTAMP"

# 兼容旧编排：若根数据目录仍来自匿名卷，则从容器复制实际运行数据。
SNAPPYMAIL_SOURCE=host
if command -v docker >/dev/null 2>&1 && docker inspect snappymail-web >/dev/null 2>&1; then
    SNAPPYMAIL_RUNTIME_SOURCE="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/snappymail"}}{{println .Source}}{{end}}{{end}}' snappymail-web 2>/dev/null | head -n 1)"
    if [ -z "$SNAPPYMAIL_RUNTIME_SOURCE" ] \
        || [ "$(realpath -m -- "$SNAPPYMAIL_RUNTIME_SOURCE")" != "$SNAPPYMAIL_DATA_PATH" ]; then
        docker cp 'snappymail-web:/var/lib/snappymail/.' "$TEMPORARY_DIR/snappymail-data"
        if [ ! -d "$TEMPORARY_DIR/snappymail-data/_data_" ]; then
            printf '[错误] 从运行容器复制的 SnappyMail 数据缺少 _data_ 目录。\n' >&2
            exit 1
        fi
        SNAPPYMAIL_SOURCE=container
        printf '[信息] SnappyMail 仍使用旧卷，本次直接从容器快照运行数据。\n'
    else
        cp -a -- "$SNAPPYMAIL_DATA_PATH/." "$TEMPORARY_DIR/snappymail-data/"
    fi
else
    cp -a -- "$SNAPPYMAIL_DATA_PATH/." "$TEMPORARY_DIR/snappymail-data/"
fi
if [ -z "$(find "$TEMPORARY_DIR/snappymail-data" -mindepth 1 -print -quit)" ]; then
    printf '[警告] SnappyMail 数据目录尚未初始化，归档中的 snappymail-data 为空。\n'
fi

if [ -f "$INGRESS_DB_PATH" ]; then
    python3 - "$INGRESS_DB_PATH" "$TEMPORARY_DIR/ingress-data/ingress_emails.db" <<'PY'
import sqlite3
import sys

source_path, snapshot_path = sys.argv[1:]
with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
    with sqlite3.connect(snapshot_path) as snapshot:
        source.backup(snapshot)
        result = snapshot.execute("PRAGMA integrity_check").fetchone()
if not result or result[0] != "ok":
    raise SystemExit("Ingress SQLite 快照完整性检查失败")
PY
else
    printf '[警告] Ingress SQLite 数据库尚不存在，归档中不包含邮件快照。\n'
fi

POSTGRES_INCLUDED=false
case "$POSTGRES_BACKUP_ENABLED" in
    true)
        if [ -z "$POSTGRES_BACKUP_CONTAINER" ] \
            || [ -z "$POSTGRES_BACKUP_DATABASE" ] \
            || [ -z "$POSTGRES_BACKUP_USER" ]; then
            printf '[错误] PostgreSQL 备份已启用，但容器名、数据库名或数据库用户未配置。\n' >&2
            exit 1
        fi
        if ! command -v docker >/dev/null 2>&1; then
            printf '[错误] PostgreSQL 备份已启用，但找不到 Docker。\n' >&2
            exit 1
        fi
        if [ "$(docker inspect --format '{{.State.Running}}' "$POSTGRES_BACKUP_CONTAINER" 2>/dev/null || true)" != "true" ]; then
            printf '[错误] PostgreSQL 容器未运行：%s\n' "$POSTGRES_BACKUP_CONTAINER" >&2
            exit 1
        fi
        POSTGRES_DUMP_NAME="$(printf '%s' "$POSTGRES_BACKUP_DATABASE" | tr -c 'A-Za-z0-9._-' '_').dump"
        docker exec "$POSTGRES_BACKUP_CONTAINER" pg_dump \
            --format=custom \
            --no-owner \
            --no-privileges \
            --username="$POSTGRES_BACKUP_USER" \
            --dbname="$POSTGRES_BACKUP_DATABASE" \
            > "$TEMPORARY_DIR/postgresql/$POSTGRES_DUMP_NAME"
        if [ ! -s "$TEMPORARY_DIR/postgresql/$POSTGRES_DUMP_NAME" ]; then
            printf '[错误] PostgreSQL 导出文件为空。\n' >&2
            exit 1
        fi
        POSTGRES_INCLUDED=true
        ;;
    false)
        printf '[警告] POSTGRES_BACKUP_ENABLED=false，本次归档不包含 PostgreSQL。\n'
        ;;
    *)
        printf '[错误] POSTGRES_BACKUP_ENABLED 只能设为 true 或 false。\n' >&2
        exit 1
        ;;
esac

cp -- "$ENV_FILE" "$TEMPORARY_DIR/metadata/environment.env"
cp -- docker-compose.yml "$TEMPORARY_DIR/metadata/docker-compose.yml"
cat > "$TEMPORARY_DIR/metadata/manifest.txt" <<EOF
format_version=1
created_at=$(date -Iseconds)
hostname=$(hostname)
postgresql_included=${POSTGRES_INCLUDED}
stalwart_config_path=${STALWART_CONFIG_PATH}
stalwart_data_path=${STALWART_DATA_PATH}
ingress_data_path=${INGRESS_DATA_PATH}
snappymail_data_path=${SNAPPYMAIL_DATA_PATH}
snappymail_source=${SNAPPYMAIL_SOURCE}
EOF

# SnappyMail 使用临时副本，SQLite 与 PostgreSQL 使用一致性快照；Stalwart 文件直接从运行目录读取。
tar -czf "$TEMPORARY_ARCHIVE" \
    -C "$(dirname "$STALWART_CONFIG_PATH")" "$STALWART_CONFIG_ROOT" \
    -C "$(dirname "$STALWART_DATA_PATH")" "$STALWART_DATA_ROOT" \
    -C "$TEMPORARY_DIR" ingress-data postgresql snappymail-data metadata

tar -tzf "$TEMPORARY_ARCHIVE" >/dev/null
chmod 600 -- "$TEMPORARY_ARCHIVE"
mv -- "$TEMPORARY_ARCHIVE" "$BACKUP_ARCHIVE"

(
    cd "$BACKUP_DIR"
    sha256sum "$ARCHIVE_NAME" > "${ARCHIVE_NAME}.sha256.tmp"
)
mv -- "${CHECKSUM_FILE}.tmp" "$CHECKSUM_FILE"
chmod 600 -- "$BACKUP_ARCHIVE" "$CHECKSUM_FILE"

# 只有新归档和校验文件都生成成功后，才清理过期备份。
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'onprs_email_backup_*.tar.gz' -o -name 'onprs_email_backup_*.tar.gz.sha256' \) \
    -mtime "+${BACKUP_RETENTION_DAYS}" -delete

printf '[完成] 备份已生成：%s\n' "$BACKUP_ARCHIVE"
printf '[信息] SHA-256 校验文件：%s\n' "$CHECKSUM_FILE"
printf '[信息] 归档大小：%s\n' "$(du -sh "$BACKUP_ARCHIVE" | awk '{print $1}')"
printf '[注意] 归档包含邮件数据和凭据，复制到异地前必须加密。\n'
