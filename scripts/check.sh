#!/usr/bin/env bash
# 统一执行仓库的静态检查与单元测试。

set -euo pipefail

STRICT=0
if [ "${1:-}" = "--strict" ]; then
    STRICT=1
elif [ "$#" -gt 0 ]; then
    printf '用法: bash scripts/check.sh [--strict]\n' >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

log_info() {
    printf '[检查] %s\n' "$1"
}

require_command() {
    local command_name="$1"
    if command -v "$command_name" >/dev/null 2>&1; then
        return 0
    fi
    if [ "$STRICT" -eq 1 ]; then
        printf '[失败] 严格模式缺少命令: %s\n' "$command_name" >&2
        exit 1
    fi
    printf '[跳过] 未安装 %s。\n' "$command_name"
    return 1
}

if ! command -v python >/dev/null 2>&1; then
    printf '[失败] 未安装 Python。\n' >&2
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    printf '[失败] 未安装 Git。\n' >&2
    exit 1
fi

log_info "检查 Shell 语法"
bash -n scripts/*.sh

log_info "运行 Ingress 单元测试"
PYTHONUTF8=1 python -m unittest discover -s services/ingress -p 'test_*.py' -v

log_info "运行注册配置单元测试"
PYTHONUTF8=1 python -m unittest discover -s scripts -p 'test_*.py' -v

log_info "运行备份归档夹具测试"
bash scripts/test-backup.sh

if require_command node; then
    log_info "检查 Cloudflare Worker JavaScript 语法"
    node --check cloudflare-worker/worker.js
fi

if require_command shellcheck; then
    log_info "运行 ShellCheck"
    shellcheck scripts/*.sh
fi

if require_command docker; then
    if docker compose version >/dev/null 2>&1; then
        log_info "展开并校验 Docker Compose 配置"
        INGRESS_SECRET_KEY=0123456789abcdef0123456789abcdef docker compose config --quiet
    elif [ "$STRICT" -eq 1 ]; then
        printf '[失败] 严格模式缺少 Docker Compose 子命令。\n' >&2
        exit 1
    else
        printf '[跳过] Docker Compose 子命令不可用。\n'
    fi
fi

log_info "检查文本行尾空白"
WHITESPACE_ERRORS=0
while IFS= read -r -d '' path; do
    if matches="$(grep -IHnE '[[:blank:]]+$' "$path" 2>/dev/null)" && [ -n "$matches" ]; then
        printf '%s\n' "$matches" >&2
        WHITESPACE_ERRORS=1
    fi
done < <(git ls-files --cached --others --exclude-standard -z)
if [ "$WHITESPACE_ERRORS" -ne 0 ]; then
    printf '[失败] 检测到行尾空白。\n' >&2
    exit 1
fi

git diff --check

printf '[完成] 仓库质量检查通过。\n'
