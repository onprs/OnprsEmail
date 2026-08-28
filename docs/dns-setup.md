# DNS 配置

本项目使用 Cloudflare Email Routing 接收邮件。根域 MX 必须指向 Cloudflare，而不是 `mail.onprs.online`；服务器公网 TCP 25 端口不可达时，后者会导致入站邮件无法送达。

## 当前记录基线

以下记录与项目既定架构一致。Cloudflare Email Routing 自动生成的 MX 值应以控制台实际显示为准，不要同时保留其他根域 MX。

| 类型 | 名称 | 值 | 代理状态 | 用途 |
| :--- | :--- | :--- | :--- | :--- |
| `A` | `mail` | `155.117.155.11` | 仅 DNS | SMTP、IMAP 与管理入口 |
| `A` | `use-mail` | `155.117.155.11` | 仅 DNS 或代理 | SnappyMail HTTPS 入口 |
| `MX` | `@` | `route2.mx.cloudflare.net`，优先级 21 | 不适用 | Cloudflare Email Routing |
| `MX` | `@` | `route1.mx.cloudflare.net`，优先级 25 | 不适用 | Cloudflare Email Routing |
| `MX` | `@` | `route3.mx.cloudflare.net`，优先级 33 | 不适用 | Cloudflare Email Routing |
| `TXT` | `@` | `v=spf1 include:_spf.mx.cloudflare.net ~all` | 不适用 | 当前 SPF 记录 |
| `TXT` | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:admin@onprs.online` | 不适用 | DMARC 策略与聚合报告 |

`mail` 必须保持“仅 DNS”，否则 Cloudflare 的普通 HTTP 代理无法转发 SMTP、IMAP、POP3 和 ManageSieve。`use-mail` 只承载 HTTPS，可按 OpenResty 和 Cloudflare 的实际配置选择代理状态。

服务器具有公网 IPv6 `2a12:bec0:167:1189::`，但 2026-08-28 的公开查询未发现 `mail.onprs.online` AAAA 记录。只有在实际验证 IPv6 入站路由、防火墙、PTR 和邮件协议证书后，才应发布对应 AAAA。

## SPF

一个域名只能发布一条 SPF 记录。该记录必须合并所有实际发信来源：Cloudflare 转发、Stalwart 使用的 SMTP Relay，以及其他获准服务。不要把多个 `v=spf1` TXT 记录并列发布。

当前线上记录可通过以下命令读取：

```bash
dig +short onprs.online TXT
```

启用或更换 SMTP Relay 后，按服务商给出的域名鉴权记录更新同一条 SPF，并确认最终查询结果没有超过 SPF 的 DNS 查询限制。`~all` 表示软失败，不应描述为严格拒绝策略。

## DKIM

DKIM 记录必须与实际执行签名的系统一致：

- 由 Stalwart 签名时，在 Stalwart 中生成选择器和密钥，再发布其显示的 TXT 记录。
- 由 SMTP Relay 签名时，按中继商要求发布 TXT 或 CNAME 记录。
- 同时使用多个签名方时，每个选择器必须唯一。

不要在未生成对应私钥和选择器前直接复制示例公钥。发布后检查完整记录，例如：

```bash
dig +short <selector>._domainkey.onprs.online TXT
```

## DMARC

当前策略使用 `p=quarantine`。提高到 `p=reject` 前，应先检查一段时间的聚合报告，确认所有合法发信源均通过 SPF 或 DKIM 对齐。策略调整后还应验证子域策略、报告地址和第三方发信服务。

## 验证

```bash
dig +short onprs.online MX
dig +short onprs.online TXT
dig +short _dmarc.onprs.online TXT
dig +short mail.onprs.online A
dig +short mail.onprs.online AAAA
dig +short use-mail.onprs.online A
```

验收条件：

- 根域只有 Cloudflare Email Routing 的三条 MX。
- `mail.onprs.online` 的 A 记录解析到当前服务器，且未开启 Cloudflare HTTP 代理。
- 若发布 AAAA，IPv6 路由、防火墙、PTR 和 TLS 必须全部通过实测。
- SPF 只有一条，并覆盖当前全部发信来源。
- DKIM 选择器与实际签名方一致。
- DMARC 报告地址可以正常收信。

DNS 验证通过后，再按 [Cloudflare Email Worker](cloudflare-worker-setup.md) 完成 Catch-all 规则并进行端到端收信测试。
