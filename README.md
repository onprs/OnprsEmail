# Onprs Email 域名邮箱服务

Onprs Email 是面向 `onprs.online` 的自托管邮件服务仓库。项目组合 Stalwart Mail Server、SnappyMail、Cloudflare Email Routing 和自研 Ingress 服务，在服务器无法使用公网 TCP 25 端口的条件下完成收信、客户端访问和出站中继。

## 架构

入站邮件采用以下链路：

```text
外部发件服务器
  -> Cloudflare Email Routing
  -> Email Worker（HTTPS + Ingress 密钥）
  -> Ingress SQLite
  -> 内部 SMTP（尽力同步至 Stalwart）
```

Ingress 在返回成功前先将邮件写入 SQLite，然后尝试投递到 Stalwart。内部 SMTP 暂时不可用时，邮件仍保留在 Ingress 数据库中，但当前实现不会自动重试 SMTP 投递。运维检查需要同时验证 Ingress API 与 Stalwart 邮箱。

出站邮件由 Stalwart 通过 SMTP Relay 的 465 或 587 端口发送。最终送达结果仍取决于中继商配置、域名鉴权、收件方策略和邮件内容，项目不对送达率作保证。

生产环境中的 Stalwart 使用既有 PostgreSQL 服务。PostgreSQL 连接由 Stalwart 管理配置维护，不在本仓库的 Compose 文件中保存数据库凭据。

## 主要能力

- Cloudflare Email Routing Catch-all 收信，不依赖服务器公网 TCP 25 入站连通性。
- Stalwart 提供 SMTP Submission、IMAP、POP3、ManageSieve 和管理接口。
- SnappyMail 提供网页邮箱。
- Ingress 提供邮件摘要、详情、附件、原始邮件和状态管理 API。
- 桌面端通过独立注册码创建固定域名下的普通邮箱账号。
- 镜像基础版本使用 digest 固定，容器日志配置轮转。
- Python 单元测试、ShellCheck、JavaScript 语法和 Compose 配置由统一检查入口及 CI 验证。

## 仓库结构

```text
Onprs_Email/
├── .github/workflows/quality.yml      # GitHub Actions 质量检查
├── cloudflare-worker/worker.js        # Cloudflare Email Worker
├── config/snappymail/domains/         # SnappyMail 域配置模板
├── docs/                              # 部署、接口与运维文档
├── scripts/
│   ├── backup.sh                      # 数据与配置备份
│   ├── check.sh                       # 统一质量检查入口
│   ├── configure-registration.py      # 配置桌面端账号创建服务
│   ├── setup.sh                       # 服务器部署脚本
│   ├── test-backup.sh                 # 备份归档夹具测试
│   ├── test_configure_registration.py # 注册配置单元测试
│   └── test-email.sh                  # 运行状态自检
├── services/ingress/
│   ├── app.py                         # Ingress 服务
│   ├── Dockerfile                     # Ingress 镜像定义
│   └── test_app.py                    # Ingress 单元测试
├── .env.example                       # 环境变量模板
└── docker-compose.yml                 # 容器编排
```

## 部署前提

目标部署环境为 Debian 13。执行部署脚本前需要确认：

- 已安装 Docker Engine 与 Docker Compose 插件。
- 当前用户为 `root`。
- 1Panel 创建的外部 Docker 网络 `1panel-network` 已存在。
- `/opt/1panel/apps/openresty/openresty/conf/ssl` 是有效的只读证书目录。
- OpenResty 已预留 4080、4081 和 4082 对应的本机反向代理入口。
- 数据目录具有足够空间，且不与现有业务目录重合。

部署脚本会创建 `.env`、生成 Ingress 密钥和桌面端注册码、校验 Compose 配置、准备持久化目录并重建三个服务容器。新部署会写入 SnappyMail 域模板；旧部署若仍使用镜像自动创建的匿名卷，脚本会先停止 SnappyMail、复制运行数据到宿主机目录并重新启动旧容器，再进行后续重建。迁移函数不会主动删除旧卷；重建完成后应先验证新挂载，再单独核对和清理无引用卷。

脚本不会自动修改 DNS、Stalwart 数据库连接、TLS、邮件域或出站 Relay 配置。

## 部署

```bash
git clone https://github.com/onprs/OnprsEmail.git
cd OnprsEmail
bash scripts/setup.sh
```

已有任一目标容器时，脚本会拒绝直接重建。完成备份并确认维护影响后，显式执行：

```bash
bash scripts/setup.sh --confirm-recreate
```

基础服务启动后，按需配置桌面端账号创建功能：

```bash
python3 scripts/configure-registration.py
```

配置顺序和安全边界见 [桌面端账号创建](docs/account-registration.md)。

## 验证

服务器本地自检：

```bash
bash scripts/test-email.sh
```

脚本检查三个容器、全部服务端口、三个 HTTP 入口和 Cloudflare MX 记录。任何核心检查失败时，脚本返回非零退出码；缺少可选的 `dig` 时只报告跳过。

Ingress 公开健康端点：

```bash
curl --fail https://mail.onprs.online/api/email-ingress/health
```

单元测试与静态检查：

```bash
bash scripts/check.sh
```

本地缺少 Docker 或 ShellCheck 时，普通模式会跳过对应检查。CI 使用严格模式，缺少任何必需工具都会失败：

```bash
bash scripts/check.sh --strict
```

## 备份

生产环境使用 PostgreSQL 时，先在 `.env` 中填写：

```dotenv
POSTGRES_BACKUP_ENABLED=true
POSTGRES_BACKUP_CONTAINER=<PostgreSQL 容器名>
POSTGRES_BACKUP_DATABASE=stalwart
POSTGRES_BACKUP_USER=<具备 pg_dump 权限的数据库用户>
```

然后执行：

```bash
bash scripts/backup.sh
```

归档包含 Stalwart 文件、SnappyMail 实际运行数据、Ingress SQLite 一致性快照、PostgreSQL 自定义格式导出、部署环境文件和 Compose 配置。旧部署尚未完成 SnappyMail 卷迁移时，备份脚本会直接从现有容器复制 `/var/lib/snappymail`，避免漏掉匿名卷中的配置。

归档含真实邮件与凭据，只能以 `0600` 权限保存，并应在异地复制前加密。备份范围、校验和恢复演练见 [备份与恢复](docs/backup-restore.md)。

## 服务入口

| 服务 | 公网地址 | 本机上游 |
| :--- | :--- | :--- |
| Stalwart 管理后台 | `https://mail.onprs.online` | `http://127.0.0.1:4080` |
| SnappyMail 网页邮箱 | `https://use-mail.onprs.online` | `http://127.0.0.1:4081` |
| Ingress API | `https://mail.onprs.online/api/email-ingress` | `http://127.0.0.1:4082` |

## 文档

- [DNS 配置](docs/dns-setup.md)
- [Cloudflare Email Worker](docs/cloudflare-worker-setup.md)
- [客户端与反向代理](docs/client-setup.md)
- [SMTP Relay](docs/relay-setup.md)
- [Ingress API](docs/ingress-api.md)
- [桌面端账号创建](docs/account-registration.md)
- [备份与恢复](docs/backup-restore.md)
