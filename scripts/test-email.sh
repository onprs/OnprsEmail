#!/usr/bin/env bash
# ==============================================================================
# Onprs Email 服务连通性与健康自检脚本
# ==============================================================================

set -euo pipefail

GREEN='\033[032m'
RED='\033[031m'
YELLOW='\033[033m'
BLUE='\033[036m'
PLAIN='\033[0m'

echo -e "${BLUE}=== 开始 Onprs Email 邮件服务连通性检查 ===${PLAIN}"

# 1. 检查 Docker 容器运行状态
echo -n "1. 容器运行状态: "
if docker ps --filter "name=stalwart-mail" --format '{{.Status}}' | grep -q "Up"; then
    echo -e "${GREEN}[正常] stalwart-mail 正在运行${PLAIN}"
else
    echo -e "${RED}[异常] stalwart-mail 未运行！${PLAIN}"
fi

# 2. 检查本地端口监听
check_port() {
    local port=$1
    local name=$2
    if ss -tulpn | grep -q ":${port} "; then
        echo -e "   - 端口 ${port} (${name}): ${GREEN}已监听${PLAIN}"
    else
        echo -e "   - 端口 ${port} (${name}): ${RED}未监听${PLAIN}"
    fi
}

echo "2. 核心端口监听自检:"
check_port 25 "SMTP Inbound"
check_port 465 "SMTPS"
check_port 587 "Submission"
check_port 993 "IMAPS"
check_port 143 "IMAP"
check_port 4080 "Stalwart WebUI (Internal HTTP)"

# 3. 检查 Stalwart Web 管理端实际响应
echo -n "3. Stalwart WebUI 健康检查: "
if curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:4080/healthz/live >/dev/null; then
    echo -e "${GREEN}[正常] HTTP 服务可访问${PLAIN}"
else
    echo -e "${RED}[异常] HTTP 服务不可访问${PLAIN}"
fi

# 4. 检查 DNS 解析
echo "4. 域名 DNS 解析核查:"
if command -v dig &>/dev/null; then
    RESOLVED_IP=$(dig +short mail.onprs.online A | tail -n1)
    echo -e "   - mail.onprs.online A 记录: ${YELLOW}${RESOLVED_IP:-未解析}${PLAIN}"
    RESOLVED_MX=$(dig +short onprs.online MX | tail -n1)
    echo -e "   - onprs.online MX 记录: ${YELLOW}${RESOLVED_MX:-未解析}${PLAIN}"
else
    echo "   - 未安装 dig 工具，跳过 DNS 命令行查询"
fi

echo -e "${BLUE}=== 检查结束 ===${PLAIN}"
