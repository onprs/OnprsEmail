# Onprs Email 客户端连接与反向代理指南

## 1. 常用邮件客户端连接参数

你可以使用任何支持标准邮件协议的客户端（如 iOS 邮件、Android FairEmail/Gmail、macOS Mail、Outlook、Foxmail、Thunderbird 等）连接你的域名邮箱。

### 1.1 发送服务器 (SMTP / 出站)
- **SMTP 服务器 (Host)**: `mail.onprs.online`
- **端口 / 加密协议**:
  - **465** (推荐: SSL/TLS / SMTPS)
  - 或 **587** (STARTTLS / Submission)
- **身份验证**: 需要身份验证（用户名全称，如 `admin@onprs.online`，以及对应密码）

### 1.2 接收服务器 (IMAP / 入站)
- **IMAP 服务器 (Host)**: `mail.onprs.online`
- **端口 / 加密协议**:
  - **993** (推荐: SSL/TLS / IMAPS)
  - 或 **143** (STARTTLS / IMAP)
- **身份验证**: 邮箱账号全称 + 密码

### 1.3 接收服务器 (POP3 / 入站，备用)
- **POP3 服务器 (Host)**: `mail.onprs.online`
- **端口 / 加密协议**:
  - **995** (SSL/TLS / POP3S)
  - 或 **110** (Plain / STARTTLS)

---

## 2. 1Panel OpenResty 反向代理与 SSL 配置

为了让 Web 管理控制台与 Webmail 通过安全 HTTPS 域名访问，可在 1Panel 的 OpenResty 中添加反向代理站点。

### 2.1 Web 管理控制台 (Stalwart Admin)
- **域名**: `mail.onprs.online`（或 `admin-mail.onprs.online`）
- **反向代理目标**: `http://127.0.0.1:4080`
- **Stalwart HTTP 设置**: 在 **Settings → Network → HTTP → General** 启用 `useXForwarded`，让 Stalwart 使用 1Panel 已发送的 `X-Forwarded-For`。
- **内部来源允许规则**: 在 **Settings → Security → Allowed IPs** 允许 `172.18.0.1/32`（1Panel 网关）与 `172.19.0.0/16`（邮件内部网络），避免内部代理和 Ingress 被自动封禁。
- **Proxy Protocol**: 1Panel 使用普通 HTTP 反向代理时，保持 `proxyTrustedNetworks` 为空；只有同时在代理端启用 TCP Proxy Protocol 时才能配置该项。
- **SSL 证书**: 在 1Panel 申请 Let's Encrypt 证书并开启 HTTPS 强制跳转。

### 2.2 网页邮箱客户端 (SnappyMail Webmail)
- **域名**: `webmail.onprs.online`（或通过路径反代）
- **反向代理目标**: `http://127.0.0.1:4081`
- **SSL 证书**: 申请 Let's Encrypt 证书并启用 HTTPS。

---

## 3. SSL 证书复用于邮件协议 (SMTP/IMAP)

Stalwart 支持两种 TLS 证书管理模式：
1. **自动 ACME**：Stalwart 可通过 TLS-ALPN-01 或 DNS 挑战直接自动从 Let's Encrypt 申请并轮换证书。
2. **复用 1Panel 证书**：`docker-compose.yml` 中已将 1Panel 的 SSL 目录挂载至容器内 `/etc/ssl/1panel`，可直接在 Stalwart Web 后台中指定对应域名的 `.crt` 和 `.key` 文件路径。
