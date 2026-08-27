# Onprs Email 域名 DNS 配置完整指南

本指南专为 `onprs.online` 域名（托管于 Cloudflare）定制，指导如何完整配置邮件收发与防垃圾邮件鉴权记录。

---

## 1. Cloudflare DNS 记录清单

> ⚠️ **重要提示（Cloudflare 专属）**：  
> 所有邮件相关的 A 记录（如 `mail`）在 Cloudflare 面板中**必须设置为 DNS Only（灰色云朵）**，绝对不能开启 Proxied（橙色云朵），否则非 HTTP 邮件协议（SMTP/IMAP）将无法连接！

| 类型 | 主机名 / 名称 (Name) | 记录值 (Content / Value) | 代理状态 (Proxy Status) | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| **A** | `mail` | `155.117.155.11` | **DNS Only (仅 DNS)** | 邮件主机 IPv4 |
| **AAAA** | `mail` | `2a12:bec0:167:1189::` | **DNS Only (仅 DNS)** | 邮件主机 IPv6 |
| **MX** | `@` | `mail.onprs.online` (优先级 10) | - | 接收发往 `@onprs.online` 的邮件 |
| **MX** | `mail` | `mail.onprs.online` (优先级 10) | - | 接收发往 `@mail.onprs.online` 的邮件 |
| **TXT** | `@` | `v=spf1 mx ~all`（或包含 relay） | - | SPF 发信主体授权 |
| **TXT** | `mail` | `v=spf1 mx ~all` | - | 子域名 SPF 授权 |
| **TXT** | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:admin@onprs.online` | - | DMARC 保护策略 |
| **TXT** | `stalwart._domainkey` | `v=DKIM1; k=rsa; p=<从 Stalwart 后台复制的公钥>` | - | DKIM 防篡改数字签名 |

---

## 2. 详细配置说明

### 2.1 SPF (Sender Policy Framework)
- 作用：告诉全球邮件服务商，哪些服务器有权使用 `@onprs.online` 域发出邮件。
- 基础配置：`v=spf1 mx ~all`
- 搭配出站中继（以 Brevo 或 Resend 为例）：
  - 若使用 Brevo：`v=spf1 mx include:spf.sendinblue.com ~all`
  - 若使用 Resend：`v=spf1 mx include:resend.com ~all`

### 2.2 DKIM (DomainKeys Identified Mail)
1. 登录 Stalwart 管理后台（`https://mail.onprs.online` 或服务器本机的 `http://127.0.0.1:4080`）。
2. 进入 **Management** -> **Directory** -> **Domains**，选择 `onprs.online`。
3. 点击 **Signatures / DKIM** 生成 RSA 2048 密钥对，Selector 设为 `stalwart`。
4. 复制生成的公钥 TXT 记录值，粘贴至 Cloudflare 的 `stalwart._domainkey` TXT 记录中。

### 2.3 DMARC (Domain-based Message Authentication)
- 主机记录：`_dmarc`
- TXT 记录值：`v=DMARC1; p=quarantine; sp=quarantine; pct=100; rua=mailto:admin@onprs.online`
- 说明：
  - `p=quarantine`：SPF 或 DKIM 鉴权失败时，将邮件标记为垃圾邮件并放入垃圾箱。
  - `rua=mailto:...`：接收日度邮件鉴权聚合分析报告。

---

## 3. DNS 生效验证

在本地或终端执行以下命令验证解析状态：

```bash
# 验证 A 记录
dig +short mail.onprs.online A

# 验证 MX 记录
dig +short onprs.online MX

# 验证 SPF
dig +short onprs.online TXT

# 验证 DKIM
dig +short stalwart._domainkey.onprs.online TXT

# 验证 DMARC
dig +short _dmarc.onprs.online TXT
```
