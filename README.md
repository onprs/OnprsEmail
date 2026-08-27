# Onprs Email 域名邮箱服务

基于 **Stalwart Mail Server**（Rust 现代化邮件服务引擎）、**PostgreSQL 18** 存储后端、**SnappyMail**（轻量 Webmail 前端）以及 **Cloudflare Email Ingress Gateway** 构建的企业级轻量全功能域名邮箱服务。

专为 `onprs.online` 打造，支持全域通配收信、安全出站中继与全客户端无缝支持。

> 📦 **线上仓库**：`https://github.com/onprs/OnprsEmail.git`（公开只读，默认分支 `main`）

---

## 🌟 核心特性与架构

- 🦀 **现代全合一核心**：Stalwart Mail Server 单容器运行，内存开销极低（~100MB），无缝直连 PostgreSQL 18。
- ⚡ **全域通配入库（Catch-all Ingress）**：
  - 基于 Cloudflare Email Routing + Worker + 本地 Ingress Gateway；
  - 免受 VPS 厂商 25 端口封锁限制，发往 `*@onprs.online` 的所有邮件秒级全自动入库，支持任意动态新邮箱。
- **桌面端账号创建**：通过独立注册码和隔离的无密码 Stalwart 注册主体在客户端内创建普通邮箱，无需访问网页后台。
- 🚀 **智能出站路由（Outbound Relay）**：
  - 原生配置 SMTP Relay（走 587/465 端口至 Brevo / Resend），解决 25 端口直发拦截问题，保证 100% 送达率。
- 🖥️ **双控制台与 Webmail**：
  - 管理后台：`https://mail.onprs.online`（Stalwart Admin）
  - 网页邮箱：`https://use-mail.onprs.online`（SnappyMail Webmail）
- 🔒 **全套安全标准**：
  - 双重 DKIM 签名（RSA 2048 + Ed25519）
  - SPF、DMARC 严格策略
  - SMTPS (465) / IMAPS (993) 全程 TLS 加密

---

## 📁 仓库结构

```text
Onprs_Email/
├── AGENTS.md                # 仓库全局规范与最新状态约定
├── README.md                # 项目快速入门与说明
├── docker-compose.yml       # 容器编排（Stalwart + Ingress + SnappyMail）
├── .env.example             # 环境变量配置模板
├── cloudflare-worker/       # Cloudflare Catch-all Worker 脚本
│   └── worker.js            # 通用全域捕获与 HTTPS 直投脚本
├── services/                # 自研微服务
│   └── ingress/             # 通用邮件 Ingress 接收网关 (Dockerfile + app.py)
├── config/                  # 配置文件模版
│   └── snappymail/domains/  # SnappyMail 预置域名与连接配置
├── scripts/                 # 自动化运维工具
│   ├── setup.sh             # 一键安装部署脚本
│   ├── configure-registration.py # 创建桌面端注册所需的隔离主体和受限 API Key
│   ├── backup.sh            # 数据与密钥自动备份脚本
│   └── test-email.sh        # 服务健康状态与端口自检脚本
└── docs/                    # 完整操作指引
    ├── dns-setup.md         # Cloudflare DNS 解析规范
    ├── relay-setup.md       # SMTP Relay 出站中继配置指南
    ├── client-setup.md      # 常用客户端与 1Panel 反代指南
    ├── ingress-api.md       # Ingress v2 查询与下载接口
    ├── account-registration.md # 桌面端创建邮箱配置指南
    └── cloudflare-worker-setup.md # Cloudflare Worker 通配配置指南
```

---

## 🚀 快速启动与运维

### 本地克隆

```bash
# HTTPS 方式（需代理）
git clone https://github.com/onprs/OnprsEmail.git

# SSH 方式
git clone git@github.com:onprs/OnprsEmail.git
```

### 服务器部署

在服务器（`sub2api_tokyo`）目录 `/opt/onprs-email` 执行：

```bash
# 启动所有服务容器
docker compose up -d

# 服务状态自检
./scripts/test-email.sh

# 一键数据备份
./scripts/backup.sh
```

---

## 🔗 访问入口汇总

| 服务 | 公网访问地址 | 内部转发端口 |
| :--- | :--- | :--- |
| **Stalwart 管理后台** | `https://mail.onprs.online` | `http://127.0.0.1:4080` |
| **SnappyMail 网页邮箱** | `https://use-mail.onprs.online` | `http://127.0.0.1:4081` |
| **Ingress 接收网关** | `https://mail.onprs.online/api/email-ingress` | `http://127.0.0.1:4082` |
