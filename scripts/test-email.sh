#!/usr/bin/env bash
# 检查容器状态、本机监听端口、HTTP 健康端点与公开 DNS。

set -u -o pipefail

GREEN='\033[32m'
RED='\033[31m'
YELLOW='\033[33m'
BLUE='\033[36m'
PLAIN='\033[0m'
FAILURES=0
WARNINGS=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    if ! source .env; then
        set +a
        printf '[错误] 无法加载 .env。\n' >&2
        exit 1
    fi
    set +a
fi

pass() {
    printf '%b[正常]%b %s\n' "$GREEN" "$PLAIN" "$1"
}

fail() {
    printf '%b[异常]%b %s\n' "$RED" "$PLAIN" "$1"
    FAILURES=$((FAILURES + 1))
}

warn() {
    printf '%b[跳过]%b %s\n' "$YELLOW" "$PLAIN" "$1"
    WARNINGS=$((WARNINGS + 1))
}

host_port() {
    local binding="$1"
    printf '%s\n' "${binding##*:}"
}

check_container() {
    local container_name="$1"
    local display_name="$2"
    local running
    running="$(docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null || true)"
    if [ "$running" = "true" ]; then
        pass "${display_name} 容器正在运行。"
    else
        fail "${display_name} 容器未运行。"
    fi
}

check_port() {
    local binding="$1"
    local display_name="$2"
    local port
    port="$(host_port "$binding")"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        fail "${display_name} 的端口配置无效：${binding}"
        return
    fi
    if ss -H -ltn | awk -v suffix=":${port}" '
        substr($4, length($4) - length(suffix) + 1) == suffix { found = 1 }
        END { exit !found }
    '; then
        pass "${display_name} 正在本机端口 ${port} 监听。"
    else
        fail "${display_name} 未在本机端口 ${port} 监听。"
    fi
}

check_http() {
    local url="$1"
    local display_name="$2"
    if curl --fail --silent --show-error --connect-timeout 3 --max-time 10 "$url" >/dev/null; then
        pass "${display_name} 可访问。"
    else
        fail "${display_name} 不可访问：${url}"
    fi
}

printf '%b=== Onprs Email 服务自检 ===%b\n' "$BLUE" "$PLAIN"

if ! command -v docker >/dev/null 2>&1; then
    fail "未安装 Docker，无法检查容器。"
else
    check_container stalwart-mail "Stalwart"
    check_container email-ingress-gateway "Ingress"
    check_container snappymail-web "SnappyMail"
fi

if ! command -v ss >/dev/null 2>&1; then
    fail "未安装 ss，无法检查监听端口。"
else
    check_port "${SMTP_PORT:-25}" "SMTP"
    check_port "${SMTPS_PORT:-465}" "SMTPS"
    check_port "${SUBMISSION_PORT:-587}" "Submission"
    check_port "${IMAP_PORT:-143}" "IMAP"
    check_port "${IMAPS_PORT:-993}" "IMAPS"
    check_port "${POP3_PORT:-110}" "POP3"
    check_port "${POP3S_PORT:-995}" "POP3S"
    check_port "${SIEVE_PORT:-4190}" "ManageSieve"
    check_port "${STALWART_HTTP_PORT:-127.0.0.1:4080}" "Stalwart HTTP"
    check_port "${SNAPPYMAIL_HTTP_PORT:-127.0.0.1:4081}" "SnappyMail HTTP"
    check_port "${INGRESS_HTTP_PORT:-127.0.0.1:4082}" "Ingress HTTP"
fi

if ! command -v curl >/dev/null 2>&1; then
    fail "未安装 curl，无法检查 HTTP 服务。"
else
    STALWART_PORT="$(host_port "${STALWART_HTTP_PORT:-127.0.0.1:4080}")"
    SNAPPYMAIL_PORT="$(host_port "${SNAPPYMAIL_HTTP_PORT:-127.0.0.1:4081}")"
    INGRESS_PORT="$(host_port "${INGRESS_HTTP_PORT:-127.0.0.1:4082}")"
    check_http "http://127.0.0.1:${STALWART_PORT}/healthz/live" "Stalwart 健康端点"
    check_http "http://127.0.0.1:${SNAPPYMAIL_PORT}/" "SnappyMail 页面"
    check_http "http://127.0.0.1:${INGRESS_PORT}/health" "Ingress 健康端点"
fi

if command -v dig >/dev/null 2>&1; then
    MX_RECORDS="$(dig +short "${MAIL_DOMAIN:-onprs.online}" MX)"
    MAIL_ADDRESS="$(dig +short "${MAIL_HOSTNAME:-mail.onprs.online}" A | tail -n 1)"
    if [[ "$MX_RECORDS" == *mx.cloudflare.net* ]]; then
        pass "MX 记录指向 Cloudflare Email Routing。"
    else
        fail "MX 记录未指向 Cloudflare Email Routing。"
    fi
    if [ -n "$MAIL_ADDRESS" ]; then
        pass "${MAIL_HOSTNAME:-mail.onprs.online} 已解析为 ${MAIL_ADDRESS}。"
    else
        fail "${MAIL_HOSTNAME:-mail.onprs.online} 没有可用的 A 记录。"
    fi
else
    warn "未安装 dig，未检查公开 DNS。"
fi

printf '%b=== 自检完成：%d 项异常，%d 项跳过 ===%b\n' "$BLUE" "$FAILURES" "$WARNINGS" "$PLAIN"
if [ "$FAILURES" -gt 0 ]; then
    exit 1
fi
