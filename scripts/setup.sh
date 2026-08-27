#!/usr/bin/env bash
# ==============================================================================
# Onprs Email 服务一键安装与部署脚本
# 目标环境: Debian 13 / sub2api_tokyo
# ==============================================================================

set -euo pipefail
umask 077

RED='\033[031m'
GREEN='\033[032m'
YELLOW='\033[033m'
BLUE='\033[036m'
PLAIN='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${PLAIN} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${PLAIN} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${PLAIN} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${PLAIN} $1"
}

wait_for_container() {
    local container_name="$1"
    local display_name="$2"
    local attempt

    for attempt in $(seq 1 30); do
        if [ "$(docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null || true)" = "true" ]; then
            return 0
        fi
        sleep 2
    done
    log_error "${display_name} 容器未进入运行状态，请执行 docker logs ${container_name} 检查。"
    return 1
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
    mv -f "$temporary_file" .env
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if ! command -v flock &>/dev/null; then
    log_error "未检测到 flock，无法安全协调部署与注册令牌轮换。"
    exit 1
fi
exec 9>.registration.lock
chmod 600 .registration.lock
flock -x 9

log_info "开始部署 Onprs Email 邮件服务系统..."

# 1. 检查 Docker 与 Docker Compose
if ! command -v docker &>/dev/null; then
    log_error "未检测到 Docker，请先安装 Docker！"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    log_error "未检测到 Docker Compose v2+，请先升级/安装 Docker Compose！"
    exit 1
fi

# 2. 准备 .env 配置文件
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        log_info "未检测到 .env 文件，已从 .env.example 复制默认配置..."
        cp .env.example .env
    else
        log_error "找不到 .env.example 模版文件！"
        exit 1
    fi
fi

# .env 包含长期令牌和注册码，只允许部署用户读取。
chmod 600 .env

# 加载环境变量
set -a
# shellcheck disable=SC1091
source .env
set +a

# 新安装生成独立的 Ingress 密钥，避免示例值进入部署环境。
if [ -z "${INGRESS_SECRET_KEY:-}" ]; then
    if command -v openssl &>/dev/null; then
        INGRESS_SECRET_KEY="$(openssl rand -hex 32)"
    elif command -v python3 &>/dev/null; then
        INGRESS_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    else
        log_error "无法生成 Ingress 密钥，请先安装 openssl 或 python3！"
        exit 1
    fi
    update_env_value "INGRESS_SECRET_KEY" "$INGRESS_SECRET_KEY"
    export INGRESS_SECRET_KEY
    log_success "已生成独立的 Ingress 通信密钥，请将其同步到 Cloudflare Worker Secret。"
elif [ "${#INGRESS_SECRET_KEY}" -lt 32 ]; then
    log_error "INGRESS_SECRET_KEY 至少需要 32 个字符，请先轮换服务器与 Cloudflare Worker 密钥。"
    exit 1
fi

# 桌面端自助创建邮箱使用独立注册码，与 Ingress 查询密钥隔离。
if [ -z "${ACCOUNT_REGISTRATION_CODE:-}" ]; then
    if command -v openssl &>/dev/null; then
        ACCOUNT_REGISTRATION_CODE="$(openssl rand -hex 32)"
    elif command -v python3 &>/dev/null; then
        ACCOUNT_REGISTRATION_CODE="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    else
        log_error "无法生成邮箱注册码，请先安装 openssl 或 python3！"
        exit 1
    fi
    update_env_value "ACCOUNT_REGISTRATION_CODE" "$ACCOUNT_REGISTRATION_CODE"
    export ACCOUNT_REGISTRATION_CODE
    log_success "已生成桌面端邮箱注册码。"
elif [ "${#ACCOUNT_REGISTRATION_CODE}" -lt 32 ]; then
    log_error "ACCOUNT_REGISTRATION_CODE 至少需要 32 个字符。"
    exit 1
fi

if [ -z "${STALWART_PROVISIONING_TOKEN:-}" ] || [ -z "${STALWART_REGISTRATION_DOMAIN_ID:-}" ]; then
    log_warn "尚未完成 Stalwart 受限注册配置，桌面端创建邮箱功能将暂不可用。"
    log_warn "运行 python3 scripts/configure-registration.py 完成配置后，再重启 email-ingress。"
fi

# 3. 创建持久化数据目录并设置安全权限
log_info "正在创建持久化数据目录..."
mkdir -p "${STALWART_CONFIG_PATH:-/opt/onprs-email/stalwart-config}"
mkdir -p "${STALWART_DATA_PATH:-/opt/onprs-email/stalwart-data}"
mkdir -p "${INGRESS_DATA_PATH:-/opt/onprs-email/ingress-data}"
mkdir -p "${SNAPPYMAIL_DATA_PATH:-/opt/onprs-email/snappymail-data}"
chown -R 2000:2000 \
    "${STALWART_CONFIG_PATH:-/opt/onprs-email/stalwart-config}" \
    "${STALWART_DATA_PATH:-/opt/onprs-email/stalwart-data}"
chmod 700 \
    "${STALWART_CONFIG_PATH:-/opt/onprs-email/stalwart-config}" \
    "${STALWART_DATA_PATH:-/opt/onprs-email/stalwart-data}"
chmod 700 \
    "${INGRESS_DATA_PATH:-/opt/onprs-email/ingress-data}" \
    "${SNAPPYMAIL_DATA_PATH:-/opt/onprs-email/snappymail-data}"

# 4. 拉取配置指定的镜像并启动容器服务
log_info "正在拉取配置指定的容器镜像并启动服务..."
docker compose pull --ignore-buildable
docker compose build --pull email-ingress
docker compose up -d --force-recreate

log_info "等待服务初始化启动..."
sleep 5

# 5. 检查所有容器与 Ingress HTTP 健康状态
wait_for_container "stalwart-mail" "Stalwart Mail Server" || exit 1
wait_for_container "email-ingress-gateway" "邮件 Ingress" || exit 1
wait_for_container "snappymail-web" "SnappyMail" || exit 1

if ! docker exec email-ingress-gateway python -c '
import json
from urllib.request import urlopen
with urlopen("http://127.0.0.1:8080/api/email-ingress/health", timeout=10) as response:
    payload = json.load(response)
if payload.get("status") != "healthy":
    raise SystemExit(1)
' >/dev/null 2>&1; then
    log_error "邮件 Ingress HTTP 健康检查失败，请执行 docker logs email-ingress-gateway 检查。"
    exit 1
fi
log_success "Stalwart、Ingress 和 SnappyMail 均已启动，Ingress HTTP 健康检查通过。"

# 6. 初始管理员凭据只在 Stalwart 自身日志中出现，避免由部署脚本重复输出。
log_info "如需查看 Stalwart 首次启动信息，请在受控终端执行 docker logs stalwart-mail。"

log_success "部署流程完成！"
log_info "桌面端邮箱注册码保存在 .env 的 ACCOUNT_REGISTRATION_CODE 中。"
echo -e "管理后台内部地址: ${YELLOW}http://${STALWART_HTTP_PORT:-127.0.0.1:4080}${PLAIN} (请配置 OpenResty HTTP 上游反向代理)"
echo -e "Webmail 页面: ${YELLOW}http://${SNAPPYMAIL_HTTP_PORT:-127.0.0.1:4081}${PLAIN}"
echo -e "请参阅 ${BLUE}docs/dns-setup.md${PLAIN} 完成域名 DNS 解析配置！"
