#!/usr/bin/env bash
# 使用合成数据验证 backup.sh 的归档、校验与 PostgreSQL 失败关闭语义。

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT

mkdir -p \
    "$FIXTURE_ROOT/bin" \
    "$FIXTURE_ROOT/stalwart-config" \
    "$FIXTURE_ROOT/stalwart-data" \
    "$FIXTURE_ROOT/ingress-data" \
    "$FIXTURE_ROOT/snappymail-data"

if ! command -v python3 >/dev/null 2>&1; then
    if ! command -v python >/dev/null 2>&1; then
        printf '[失败] 备份测试需要 Python。\n' >&2
        exit 1
    fi
    printf '#!/usr/bin/env bash\nexec python "$@"\n' > "$FIXTURE_ROOT/bin/python3"
    chmod +x "$FIXTURE_ROOT/bin/python3"
fi
export PATH="$FIXTURE_ROOT/bin:$PATH"

printf 'stalwart-config\n' > "$FIXTURE_ROOT/stalwart-config/config.toml"
printf 'stalwart-data\n' > "$FIXTURE_ROOT/stalwart-data/blob.dat"
printf 'snappymail-data\n' > "$FIXTURE_ROOT/snappymail-data/settings.ini"
python3 - "$FIXTURE_ROOT/ingress-data/ingress_emails.db" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    connection.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY, subject TEXT)")
    connection.execute("INSERT INTO emails (subject) VALUES (?)", ("备份测试",))
PY

write_env() {
    local path="$1"
    local postgres_enabled="$2"
    local include_postgres_settings="$3"

    {
        printf 'STALWART_CONFIG_PATH=%s\n' "$FIXTURE_ROOT/stalwart-config"
        printf 'STALWART_DATA_PATH=%s\n' "$FIXTURE_ROOT/stalwart-data"
        printf 'INGRESS_DATA_PATH=%s\n' "$FIXTURE_ROOT/ingress-data"
        printf 'SNAPPYMAIL_DATA_PATH=%s\n' "$FIXTURE_ROOT/snappymail-data"
        printf 'POSTGRES_BACKUP_ENABLED=%s\n' "$postgres_enabled"
        printf 'BACKUP_RETENTION_DAYS=14\n'
        if [ "$include_postgres_settings" = "true" ]; then
            printf 'POSTGRES_BACKUP_CONTAINER=test-postgres\n'
            printf 'POSTGRES_BACKUP_DATABASE=stalwart\n'
            printf 'POSTGRES_BACKUP_USER=backup-user\n'
        fi
    } > "$path"
}

write_env "$FIXTURE_ROOT/files-only.env" false false
mkdir -p "$FIXTURE_ROOT/files-only-backups" "$FIXTURE_ROOT/files-only-verify"
ENV_FILE="$FIXTURE_ROOT/files-only.env" \
BACKUP_DIR="$FIXTURE_ROOT/files-only-backups" \
bash "$PROJECT_ROOT/scripts/backup.sh" >/dev/null

FILES_ONLY_ARCHIVE="$(find "$FIXTURE_ROOT/files-only-backups" -maxdepth 1 -name 'onprs_email_backup_*.tar.gz' -print -quit)"
if [ -z "$FILES_ONLY_ARCHIVE" ]; then
    printf '[失败] 未生成文件级测试归档。\n' >&2
    exit 1
fi
(
    cd "$FIXTURE_ROOT/files-only-backups"
    sha256sum --check "$(basename "$FILES_ONLY_ARCHIVE").sha256" >/dev/null
)
tar -xzf "$FILES_ONLY_ARCHIVE" \
    -C "$FIXTURE_ROOT/files-only-verify" \
    ingress-data/ingress_emails.db \
    snappymail-data/settings.ini \
    metadata/manifest.txt
python3 - "$FIXTURE_ROOT/files-only-verify/ingress-data/ingress_emails.db" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    count = connection.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
if integrity != "ok" or count != 1:
    raise SystemExit(f"SQLite 快照无效：integrity={integrity}, count={count}")
PY
grep -q '^postgresql_included=false$' "$FIXTURE_ROOT/files-only-verify/metadata/manifest.txt"
grep -q '^snappymail_source=host$' "$FIXTURE_ROOT/files-only-verify/metadata/manifest.txt"
test -s "$FIXTURE_ROOT/files-only-verify/snappymail-data/settings.ini"

write_env "$FIXTURE_ROOT/incomplete.env" true false
mkdir -p "$FIXTURE_ROOT/incomplete-backups"
if ENV_FILE="$FIXTURE_ROOT/incomplete.env" \
    BACKUP_DIR="$FIXTURE_ROOT/incomplete-backups" \
    bash "$PROJECT_ROOT/scripts/backup.sh" >/dev/null 2>&1; then
    printf '[失败] PostgreSQL 参数缺失时备份不应成功。\n' >&2
    exit 1
fi
if find "$FIXTURE_ROOT/incomplete-backups" -maxdepth 1 -name 'onprs_email_backup_*.tar.gz' | grep -q .; then
    printf '[失败] PostgreSQL 参数缺失后不应留下已发布归档。\n' >&2
    exit 1
fi

cat > "$FIXTURE_ROOT/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
    inspect)
        if [[ "$*" == *snappymail-web* ]] && [[ "$*" == *Destination* ]]; then
            printf '/legacy/snappymail-volume\n'
        elif [[ "$*" == *State.Running* ]]; then
            printf 'true\n'
        fi
        ;;
    cp)
        if [ "${FAKE_DOCKER_CP_FAIL:-false}" = "true" ]; then
            exit 1
        fi
        destination="${@: -1}"
        mkdir -p "$destination/_data_/_default_/domains"
        printf 'legacy-snappymail-data\n' > "$destination/_data_/_default_/domains/onprs.online.json"
        ;;
    exec)
        printf 'synthetic-postgresql-dump\n'
        ;;
    *)
        exit 2
        ;;
esac
SH
chmod +x "$FIXTURE_ROOT/bin/docker"

write_env "$FIXTURE_ROOT/complete.env" true true
mkdir -p "$FIXTURE_ROOT/complete-backups" "$FIXTURE_ROOT/complete-verify"
ENV_FILE="$FIXTURE_ROOT/complete.env" \
BACKUP_DIR="$FIXTURE_ROOT/complete-backups" \
bash "$PROJECT_ROOT/scripts/backup.sh" >/dev/null

COMPLETE_ARCHIVE="$(find "$FIXTURE_ROOT/complete-backups" -maxdepth 1 -name 'onprs_email_backup_*.tar.gz' -print -quit)"
tar -xzf "$COMPLETE_ARCHIVE" \
    -C "$FIXTURE_ROOT/complete-verify" \
    postgresql/stalwart.dump \
    snappymail-data/_data_/_default_/domains/onprs.online.json \
    metadata/manifest.txt
test -s "$FIXTURE_ROOT/complete-verify/postgresql/stalwart.dump"
test -s "$FIXTURE_ROOT/complete-verify/snappymail-data/_data_/_default_/domains/onprs.online.json"
grep -q '^postgresql_included=true$' "$FIXTURE_ROOT/complete-verify/metadata/manifest.txt"
grep -q '^snappymail_source=container$' "$FIXTURE_ROOT/complete-verify/metadata/manifest.txt"

mkdir -p "$FIXTURE_ROOT/copy-failure-backups"
if FAKE_DOCKER_CP_FAIL=true \
    ENV_FILE="$FIXTURE_ROOT/complete.env" \
    BACKUP_DIR="$FIXTURE_ROOT/copy-failure-backups" \
    bash "$PROJECT_ROOT/scripts/backup.sh" >/dev/null 2>&1; then
    printf '[失败] SnappyMail 容器复制失败时备份不应成功。\n' >&2
    exit 1
fi
if find "$FIXTURE_ROOT/copy-failure-backups" -maxdepth 1 -name 'onprs_email_backup_*.tar.gz' | grep -q .; then
    printf '[失败] SnappyMail 容器复制失败后不应留下已发布归档。\n' >&2
    exit 1
fi

printf '[完成] 备份夹具测试通过。\n'
