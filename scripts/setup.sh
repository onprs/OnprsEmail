#!/usr/bin/env bash
# Onprs Email 安装与部署脚本，目标环境为 Debian 13。

set -Eeuo pipefail
umask 077

RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[36m'
PLAIN='\033[0m'
CONFIRM_RECREATE=0

log_info() {
    printf '%b[信息]%b %s\n' "$BLUE" "$PLAIN" "$1"
}

log_success() {
    printf '%b[完成]%b %s\n' "$GREEN" "$PLAIN" "$1"
}

log_warn() {
    printf '%b[警告]%b %s\n' "$YELLOW" "$PLAIN" "$1"
}

log_error() {
    printf '%b[错误]%b %s\n' "$RED" "$PLAIN" "$1" >&2
}

wait_for_container() {
    local container_name="$1"
    local display_name="$2"
    local attempt

    for ((attempt = 1; attempt <= 30; attempt++)); do
        if [ "$(docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null || true)" = "true" ]; then
            return 0
        fi
        sleep 2
    done
    log_error "${display_name} 容器未进入运行状态，请执行 docker logs ${container_name} 检查日志。"
    return 1
}

wait_for_ingress_health() {
    local attempt

    for ((attempt = 1; attempt <= 30; attempt++)); do
        if docker exec email-ingress-gateway python -c '
import json
from urllib.request import urlopen
with urlopen("http://127.0.0.1:8080/health", timeout=3) as response:
    payload = json.load(response)
if response.status != 200 or payload.get("status") != "healthy":
    raise SystemExit(1)
' >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    log_error "Ingress HTTP 健康检查失败，请执行 docker logs email-ingress-gateway 检查日志。"
    return 1
}

canonical_data_path() {
    local path="$1"
    local label="$2"
    local resolved

    if [[ "$path" != /* ]]; then
        log_error "${label} 必须使用绝对路径，当前值为：${path}"
        return 1
    fi
    resolved="$(realpath -m -- "$path")"
    case "$resolved" in
        / | /bin | /boot | /dev | /etc | /home | /lib | /lib64 | /opt | /proc | /root | /run | /sbin | /srv | /sys | /tmp | /usr | /var)
            log_error "${label} 指向危险的系统目录：${resolved}"
            return 1
            ;;
    esac
    printf '%s\n' "$resolved"
}

update_env_value() {
    local key="$1"
    local value="$2"
    local temporary_file
    local line
    local found=0

    temporary_file="$(mktemp .env.tmp.XXXXXX)"
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" == "${key}="* ]]; then
            printf '%s=%s\n' "$key" "$value" >> "$temporary_file"
            found=1
        else
            printf '%s\n' "$line" >> "$temporary_file"
        fi
    done < .env
    if [ "$found" -eq 0 ]; then
        printf '%s=%s\n' "$key" "$value" >> "$temporary_file"
    fi
    chmod 600 "$temporary_file"
    mv -f -- "$temporary_file" .env
}

generate_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    else
        log_error "无法生成密钥，请先安装 openssl 或 python3。"
        return 1
    fi
}

# 旧编排只挂载了空的 data 子目录，真实 SnappyMail 数据仍位于镜像匿名卷。
migrate_snappymail_data() {
    local current_source
    local current_source_resolved
    local staging_directory
    local was_running

    if ! docker inspect snappymail-web >/dev/null 2>&1; then
        return 0
    fi

    current_source="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/snappymail"}}{{println .Source}}{{end}}{{end}}' snappymail-web 2>/dev/null | head -n 1)"
    if [ -n "$current_source" ]; then
        current_source_resolved="$(realpath -m -- "$current_source")"
        if [ "$current_source_resolved" = "$SNAPPYMAIL_DATA_PATH" ]; then
            return 0
        fi
    fi

    if [ -n "$(find "$SNAPPYMAIL_DATA_PATH" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
        log_error "SnappyMail 仍使用旧卷，但目标目录不为空，已拒绝自动覆盖：${SNAPPYMAIL_DATA_PATH}"
        return 1
    fi

    staging_directory="$(mktemp -d "$(dirname "$SNAPPYMAIL_DATA_PATH")/.snappymail-migration.XXXXXX")"
    was_running="$(docker inspect --format '{{.State.Running}}' snappymail-web 2>/dev/null || true)"
    if [ "$was_running" = "true" ]; then
        log_info "暂停 SnappyMail 以迁移旧匿名卷。"
        docker stop --timeout 30 snappymail-web >/dev/null
    fi

    if ! docker cp 'snappymail-web:/var/lib/snappymail/.' "$staging_directory"; then
        rm -rf -- "$staging_directory"
        if [ "$was_running" = "true" ]; then
            docker start snappymail-web >/dev/null || true
        fi
        log_error "无法从现有 SnappyMail 容器复制运行数据。"
        return 1
    fi
    if [ ! -d "$staging_directory/_data_" ]; then
        rm -rf -- "$staging_directory"
        if [ "$was_running" = "true" ]; then
            docker start snappymail-web >/dev/null || true
        fi
        log_error "SnappyMail 迁移副本缺少 _data_ 目录，已保留原容器和旧卷。"
        return 1
    fi

    if ! rmdir -- "$SNAPPYMAIL_DATA_PATH"; then
        rm -rf -- "$staging_directory"
        if [ "$was_running" = "true" ]; then
            docker start snappymail-web >/dev/null || true
        fi
        log_error "SnappyMail 目标目录在迁移期间发生变化，已停止迁移。"
        return 1
    fi
    if ! mv -- "$staging_directory" "$SNAPPYMAIL_DATA_PATH"; then
        mkdir -p -- "$SNAPPYMAIL_DATA_PATH"
        rm -rf -- "$staging_directory"
        if [ "$was_running" = "true" ]; then
            docker start snappymail-web >/dev/null || true
        fi
        log_error "无法发布 SnappyMail 迁移目录，旧容器和旧卷仍保留。"
        return 1
    fi
    if [ ! -d "$SNAPPYMAIL_DATA_PATH/_data_" ]; then
        if [ "$was_running" = "true" ]; then
            docker start snappymail-web >/dev/null || true
        fi
        log_error "迁移后的 SnappyMail 数据缺少 _data_ 目录；旧容器和旧卷仍保留。"
        return 1
    fi
    if [ "$was_running" = "true" ]; then
        docker start snappymail-web >/dev/null
    fi
    chown -R 82:82 -- "$SNAPPYMAIL_DATA_PATH"
    log_success "已将 SnappyMail 运行数据迁移到 ${SNAPPYMAIL_DATA_PATH}；迁移步骤未主动删除旧匿名卷。"
}

# 新数据目录只写入已验证的域模板，其余运行文件由 SnappyMail 首次启动生成。
seed_snappymail_domains() {
    local template_path="${SCRIPT_DIR}/config/snappymail/domains/onprs.online.json"
    local domains_path="${SNAPPYMAIL_DATA_PATH}/_data_/_default_/domains"
    local target_name

    if [ -n "$(find "$SNAPPYMAIL_DATA_PATH" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
        return 0
    fi
    if [ "${MAIL_DOMAIN:-onprs.online}" != "onprs.online" ]; then
        log_warn "MAIL_DOMAIN 不是 onprs.online，未自动写入 SnappyMail 域模板。"
        return 0
    fi
    if [ ! -f "$template_path" ]; then
        log_error "找不到 SnappyMail 域配置模板：${template_path}"
        return 1
    fi

    mkdir -p -- "$domains_path"
    for target_name in default.json onprs.online.json mail.onprs.online.json use-mail.onprs.online.json; do
        cp -- "$template_path" "$domains_path/$target_name"
        chmod 640 -- "$domains_path/$target_name"
    done
    log_success "已写入 SnappyMail 域配置模板。"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

case "${1:-}" in
    "")
        ;;
    --confirm-recreate)
        CONFIRM_RECREATE=1
        ;;
    --help)
        printf '用法: bash scripts/setup.sh [--confirm-recreate]\n'
        exit 0
        ;;
    *)
        log_error "未知参数：${1}"
        printf '用法: bash scripts/setup.sh [--confirm-recreate]\n' >&2
        exit 2
        ;;
esac
if [ "$#" -gt 1 ]; then
    log_error "部署脚本最多接受一个参数。"
    exit 2
fi

for command_name in docker flock realpath; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log_error "未检测到必需命令：${command_name}。"
        exit 1
    fi
done

if [ "$(id -u)" -ne 0 ]; then
    log_error "部署脚本需要 root 权限，以便创建目录并设置 Stalwart 数据目录属主。"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    log_error "未检测到 Docker Compose 插件。"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    log_error "Docker 守护进程不可用。"
    exit 1
fi
if ! docker network inspect 1panel-network >/dev/null 2>&1; then
    log_error "外部网络 1panel-network 不存在；请先确认 1Panel 网络，再执行部署。"
    exit 1
fi

EXISTING_CONTAINERS=""
for container_name in stalwart-mail email-ingress-gateway snappymail-web; do
    if docker inspect "$container_name" >/dev/null 2>&1; then
        EXISTING_CONTAINERS="${EXISTING_CONTAINERS} ${container_name}"
    fi
done
if [ -n "$EXISTING_CONTAINERS" ] && [ "$CONFIRM_RECREATE" -ne 1 ]; then
    log_error "检测到现有容器：${EXISTING_CONTAINERS# }"
    log_error "本脚本会重建三个服务。确认备份与影响范围后，使用 --confirm-recreate 再次执行。"
    exit 1
fi

exec 9>.registration.lock
chmod 600 .registration.lock
flock -x 9

log_info "开始部署 Onprs Email。"

if [ ! -f .env ]; then
    if [ ! -f .env.example ]; then
        log_error "找不到环境变量模板 .env.example。"
        exit 1
    fi
    cp -- .env.example .env
    log_info "已从 .env.example 创建 .env。"
fi
chmod 600 .env

set -a
# shellcheck disable=SC1091
source .env
set +a

if [ -z "${INGRESS_SECRET_KEY:-}" ]; then
    INGRESS_SECRET_KEY="$(generate_secret)"
    update_env_value INGRESS_SECRET_KEY "$INGRESS_SECRET_KEY"
    export INGRESS_SECRET_KEY
    log_success "已生成 Ingress 密钥；请将同一值写入 Cloudflare Worker Secret。"
elif [ "${#INGRESS_SECRET_KEY}" -lt 32 ]; then
    log_error "INGRESS_SECRET_KEY 至少需要 32 个字符。"
    exit 1
fi

if [ -z "${ACCOUNT_REGISTRATION_CODE:-}" ]; then
    ACCOUNT_REGISTRATION_CODE="$(generate_secret)"
    update_env_value ACCOUNT_REGISTRATION_CODE "$ACCOUNT_REGISTRATION_CODE"
    export ACCOUNT_REGISTRATION_CODE
    log_success "已生成桌面端邮箱注册码。"
elif [ "${#ACCOUNT_REGISTRATION_CODE}" -lt 32 ]; then
    log_error "ACCOUNT_REGISTRATION_CODE 至少需要 32 个字符。"
    exit 1
fi

if [ -z "${STALWART_PROVISIONING_TOKEN:-}" ] || [ -z "${STALWART_REGISTRATION_DOMAIN_ID:-}" ]; then
    log_warn "Stalwart 受限注册配置尚未完成，桌面端创建邮箱功能暂不可用。"
    log_warn "基础服务启动后，运行 python3 scripts/configure-registration.py 完成配置。"
fi

if [ "${POSTGRES_BACKUP_ENABLED:-true}" = "true" ] && {
    [ -z "${POSTGRES_BACKUP_CONTAINER:-}" ] \
        || [ -z "${POSTGRES_BACKUP_DATABASE:-}" ] \
        || [ -z "${POSTGRES_BACKUP_USER:-}" ];
}; then
    log_warn "PostgreSQL 备份参数尚未填写；请设置容器名、数据库名和数据库用户。"
    log_warn "配置完成前，backup.sh 会拒绝生成不完整备份。"
fi

STALWART_CONFIG_PATH="$(canonical_data_path "${STALWART_CONFIG_PATH:-/opt/onprs-email/stalwart-config}" "Stalwart 配置目录")"
STALWART_DATA_PATH="$(canonical_data_path "${STALWART_DATA_PATH:-/opt/onprs-email/stalwart-data}" "Stalwart 数据目录")"
INGRESS_DATA_PATH="$(canonical_data_path "${INGRESS_DATA_PATH:-/opt/onprs-email/ingress-data}" "Ingress 数据目录")"
SNAPPYMAIL_DATA_PATH="$(canonical_data_path "${SNAPPYMAIL_DATA_PATH:-/opt/onprs-email/snappymail-data}" "SnappyMail 数据目录")"
export STALWART_CONFIG_PATH STALWART_DATA_PATH INGRESS_DATA_PATH SNAPPYMAIL_DATA_PATH

if [ "$STALWART_CONFIG_PATH" = "$STALWART_DATA_PATH" ] \
    || [ "$STALWART_CONFIG_PATH" = "$INGRESS_DATA_PATH" ] \
    || [ "$STALWART_CONFIG_PATH" = "$SNAPPYMAIL_DATA_PATH" ] \
    || [ "$STALWART_DATA_PATH" = "$INGRESS_DATA_PATH" ] \
    || [ "$STALWART_DATA_PATH" = "$SNAPPYMAIL_DATA_PATH" ] \
    || [ "$INGRESS_DATA_PATH" = "$SNAPPYMAIL_DATA_PATH" ]; then
    log_error "四个持久化目录必须彼此独立。"
    exit 1
fi

log_info "校验 Docker Compose 配置。"
docker compose config --quiet

log_info "创建持久化目录并设置权限。"
mkdir -p -- "$STALWART_CONFIG_PATH" "$STALWART_DATA_PATH" "$INGRESS_DATA_PATH" "$SNAPPYMAIL_DATA_PATH"
migrate_snappymail_data
seed_snappymail_domains
chown -R 2000:2000 -- "$STALWART_CONFIG_PATH" "$STALWART_DATA_PATH"
chown -R 82:82 -- "$SNAPPYMAIL_DATA_PATH"
chmod 700 -- "$STALWART_CONFIG_PATH" "$STALWART_DATA_PATH" "$INGRESS_DATA_PATH" "$SNAPPYMAIL_DATA_PATH"

log_info "拉取固定镜像并构建 Ingress。"
docker compose pull --ignore-buildable
docker compose build --pull email-ingress
docker compose up -d --force-recreate

wait_for_container stalwart-mail "Stalwart" || exit 1
wait_for_container email-ingress-gateway "Ingress" || exit 1
wait_for_container snappymail-web "SnappyMail" || exit 1

wait_for_ingress_health || exit 1

log_success "三个容器均已运行，Ingress HTTP 健康检查通过。"
log_info "如需查看 Stalwart 首次启动信息，请在受控终端执行 docker logs stalwart-mail。"
printf '管理后台内部地址: %bhttp://%s%b\n' "$YELLOW" "${STALWART_HTTP_PORT:-127.0.0.1:4080}" "$PLAIN"
printf 'Webmail 内部地址: %bhttp://%s%b\n' "$YELLOW" "${SNAPPYMAIL_HTTP_PORT:-127.0.0.1:4081}" "$PLAIN"
printf '后续步骤: 配置 DNS、TLS 与出站中继，再执行 bash scripts/test-email.sh。\n'
