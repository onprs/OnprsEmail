# AGENTS.md - Onprs Email 域名邮箱服务仓库约定

本文档作为本仓库（`Onprs_Email`）的全局开发、部署和维护规范。所有参与本项目的 AI 智能体与开发者均须遵循以下约定。

---

## 0. 远程代码仓库与版本管理

### 0.1 线上仓库
- **远程仓库地址**：`https://github.com/onprs/OnprsEmail.git`
- **默认分支**：`main`
- **远程名称**：`origin`
- **权限说明**：该仓库为公开只读仓库，任何 Agent 或协作者均不得向该仓库泄露密钥、令牌、密码等敏感信息。

### 0.2 提交规范
- 所有提交必须携带明确的中文提交信息，说明本次改动目的，严禁使用无意义信息。
- 提交前必须确认未将敏感信息（`.env`、密钥、Token、密码、私钥等）纳入版本控制。
- 提交必须保持仓库整洁，不得提交编译产物、临时文件、缓存目录及测试残留。

### 0.3 推送与同步
- 推送前必须确认本地 `origin` 指向正确的线上仓库地址。
- 推送前建议执行 `git status` 与 `git log --oneline -5` 核对待推送内容。
- 远端 `main` 分支为唯一发布渠道，禁止向其他分支直接推送。
- 提交与推送操作必须经过本地验证后再执行。

---

## 1. 项目概述与基础设施环境

### 1.1 部署目标与域名分配
- **目标主机**：OpenSSH Host `sub2api_tokyo`
- **操作系统**：Debian GNU/Linux 13 (trixie), x86_64
- **公网网络**：
  - IPv4: `155.117.155.11`
  - IPv6: `2a12:bec0:167:1189::`
- **邮件服务主域名**：`onprs.online`
- **各服务域名与访问入口**：
  - **Stalwart 管理后台**：`https://mail.onprs.online`（反代至 `127.0.0.1:4080`）
  - **SnappyMail 网页邮箱**：`https://use-mail.onprs.online`（反代至 `127.0.0.1:4081`）
  - **Ingress 通用接收接口**：`https://mail.onprs.online/api/email-ingress`（反代至 `127.0.0.1:4082`）
- **基础运行环境**：
  - 容器引擎：Docker Engine 与 Docker Compose 插件（当前服务器版本为 v5.3.1）
  - 现有管理面板：1Panel（端口 11451）
  - 现有反向代理：OpenResty（占用 80/443，统一管理 SSL 证书与反代）
  - 现有数据库容器：PostgreSQL 18.x（容器 `1Panel-postgresql-G4sf`，数据库 `stalwart`，接入 `1panel-network`）

### 1.2 网络特征与邮件收发架构实测定型
1. **云厂商网络约束**：VPS 提供商（Bytevirt）对 25 端口出入站实施了硬性防火墙拦截。
2. **入站邮件架构（Inbound Ingress）**：
   - 采用 **Cloudflare Email Routing（Catch-all）+ Email Worker + Ingress** 架构；
   - 发往 `*@onprs.online` 的邮件由 Cloudflare 接收，Worker 通过 HTTPS POST 转发到 Ingress；
   - Ingress 校验 `X-Ingress-Secret`，先持久化至 SQLite，再尝试通过内部 SMTP 同步到 Stalwart；
   - 该链路不依赖服务器公网 TCP 25 入站连通性。内部 SMTP 失败时，邮件会保留在 Ingress 数据库中，当前实现不会自动重试。
3. **出站邮件架构（Outbound Relay）**：
   - Stalwart 配置默认 MTA Outbound Route 指向上游 SMTP Relay（走开放的 587 STARTTLS / 465 SSL 端口，如 Brevo / Resend）。
4. **客户端直连协议端口**：
   - `465` (SMTPS - Implicit TLS) / `587` (Submission - STARTTLS)
   - `993` (IMAPS - Implicit TLS) / `143` (IMAP - STARTTLS)
   - `4190` (ManageSieve)

---

## 2. 服务架构与容器编排

### 2.1 核心服务组件
1. **Stalwart Mail Server (`stalwart-mail`)**：
   - Rust 邮件服务核心，生产环境使用既有 PostgreSQL 18 存储后端；
   - 接入外部 Docker 网络 `1panel-network` 与内部 `mail-network`。
2. **Ingress 接收服务 (`email-ingress-gateway`)**：
   - Python 标准库微服务，宿主机入口监听 `127.0.0.1:4082`；
   - 对接 Cloudflare Worker，负责 Ingress 密钥鉴权、SQLite 持久化、邮件解析和内部 SMTP 投递。
3. **SnappyMail Webmail (`snappymail-web`)**：
   - 网页邮箱客户端，宿主机入口监听 `127.0.0.1:4081`；
   - 运行数据统一挂载到 `/var/lib/snappymail`。新部署由 `setup.sh` 写入 `onprs.online` 及入口域名模板，旧匿名卷由部署脚本迁移到宿主机后再重建。

---

## 3. DNS 与邮件安全规范 (Cloudflare)

以下公开 DNS 状态于 2026-08-28 通过 `1.1.1.1` 核对；修改前需重新查询实际记录。

| 记录类型 | 主机记录 / 名称 | 记录值 | 代理状态 | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| **A** | `mail` | `155.117.155.11` | **仅 DNS** | 邮件管理后台与 SMTP/IMAP 主机 |
| **A** | `use-mail` | `155.117.155.11` | **仅 DNS 或已代理** | Webmail 访问域名 |
| **MX** | `@` | `route2.mx.cloudflare.net` / `route1.mx.cloudflare.net` / `route3.mx.cloudflare.net`（优先级 21 / 25 / 33） | - | Cloudflare Email Routing 接收节点 |
| **TXT (SPF)** | `@` | `v=spf1 include:_spf.mx.cloudflare.net ~all` | - | 当前公开 SPF；启用 Relay 后需按服务商要求合并发信源 |
| **TXT (DKIM)** | `<selector>._domainkey` | 由实际签名方提供 | - | 当前公开 DNS 未发现文档原示例中的两个 Stalwart 选择器，发布前必须核对实际签名配置 |
| **TXT (DMARC)**| `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:admin@onprs.online` | - | DMARC 保护策略与聚合报告 |

---

## 4. 仓库目录结构约定

```text
Onprs_Email/
├── AGENTS.md                # 仓库全局规范与最新状态约定文档
├── README.md                # 项目简介与使用说明
├── docker-compose.yml       # Stalwart + Ingress + SnappyMail 容器编排定义
├── .env.example             # 环境变量模板
├── .github/workflows/       # 持续集成质量检查
├── cloudflare-worker/       # Cloudflare Catch-all Email Worker 脚本
│   └── worker.js            # 通用全域捕获与 HTTPS 直投脚本
├── services/                # 专有微服务源码
│   └── ingress/             # Ingress 服务
│       ├── Dockerfile       # 镜像构建与健康检查定义
│       ├── app.py           # HTTP API、SQLite 持久化与内部 SMTP 投递
│       └── test_app.py      # Ingress 单元测试
├── config/                  # 配置文件模板
│   └── snappymail/          # SnappyMail 预置域名与连接配置
│       └── domains/
│           └── onprs.online.json
├── scripts/                 # 运维自动化脚本
│   ├── setup.sh             # 部署与环境初始化脚本
│   ├── backup.sh            # SQLite、PostgreSQL、服务文件与凭据备份
│   ├── check.sh             # 统一质量检查入口
│   ├── configure-registration.py # 桌面端账号创建配置脚本
│   ├── test-backup.sh        # 备份归档夹具测试
│   ├── test_configure_registration.py # 注册配置单元测试
│   └── test-email.sh        # 服务状态、端口、HTTP 与 DNS 自检
└── docs/                    # 完整操作指引与架构文档
    ├── dns-setup.md         # DNS 记录完整配置指南
    ├── relay-setup.md       # SMTP Relay 出站中继配置指南
    ├── client-setup.md      # 邮件客户端与 1Panel 反代指引
    ├── ingress-api.md       # Ingress 接收、查询与账号创建接口
    ├── account-registration.md # 桌面端账号创建指南
    ├── backup-restore.md    # 备份范围、校验与恢复演练
    └── cloudflare-worker-setup.md # Cloudflare Worker 配置指南
```

---

## 5. 开发与修改约定

1. **语言规范**：项目内所有文档、注释、提交信息、说明一律使用中文。
2. **非侵入式部署**：
   - 严禁破坏 `sub2api_tokyo` 上已运行的 1Panel、PostgreSQL、Redis、OpenResty 及其他在线生产业务；
   - 容器服务接入外部 `1panel-network` 网络，禁止私自篡改宿主机全局网络配置。
3. **安全与敏感数据**：
   - 严禁将任何明文密码、Relay API 密钥、Ingress 密钥、DKIM 私钥提交至 Git 仓库；
   - 统一使用 `.env` 与挂载卷管理敏感参数；
   - 涉及密钥轮换、临时调试、敏感日志时，必须先行确认 `.gitignore` 规则覆盖后再操作。
4. **工具使用要求**：
   - 修改文件时使用原生 `edit` 或 `write` 工具，严禁使用 `apply_patch`；
   - 所有网络配置、外部接口、端口映射必须经过实际网络测试验证，不得凭经验假设；
   - 涉及版本控制与远程仓库的操作，须遵循第 0 节远程代码仓库与版本管理规范。
