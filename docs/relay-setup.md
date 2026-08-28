# SMTP Relay 配置

目标服务器无法通过公网 TCP 25 端口直连收件方 MX，因此 Stalwart 的外发邮件必须交给支持 465 或 587 的 SMTP Relay。Relay 可以解决网络出口限制，但不能保证最终送达；收件方仍会根据 SPF、DKIM、DMARC、信誉和内容策略处理邮件。

## 选择服务商

选择 Relay 时应核对：

- 是否支持当前业务类型和预计发送量。
- 是否提供 587 STARTTLS 或 465 隐式 TLS。
- 是否支持自定义域名的 SPF、DKIM 与回信地址对齐。
- 退信、投诉、限流和日志的保留方式。
- 凭据轮换、来源地址限制和多因素认证能力。
- 当前价格、配额和区域限制。

Brevo、Resend、Amazon SES 等服务均可作为候选，但具体配额和价格会变化，应以服务商当前官方文档与控制台为准。

## 配置 Stalwart

Stalwart 管理界面的菜单名称会随版本变化。当前目标是创建默认出站路由，并填写 Relay 提供的参数：

| 配置项 | 值 |
| :--- | :--- |
| 协议 | SMTP |
| 主机 | Relay 提供的 SMTP 主机名 |
| 端口 | `587` 或 `465` |
| TLS 模式 | 587 使用 STARTTLS；465 使用隐式 TLS |
| 身份验证 | Relay 提供的 SMTP 用户名与专用密码或令牌 |

不要把 Relay 凭据写入仓库、文档、截图或普通日志。保存后先使用服务商的连接测试功能，再从普通业务账号发送测试邮件。

## 域名鉴权

配置 Relay 后，按服务商要求完成：

1. 将 Relay 发信源合并到根域唯一的 SPF 记录。
2. 发布 Relay 提供的 DKIM TXT 或 CNAME 记录。
3. 确认 Header From、Envelope From 和 DKIM 签名域满足 DMARC 对齐。
4. 检查 DMARC 聚合报告，再决定是否提高策略强度。

DNS 配置原则见 [DNS 配置](dns-setup.md)。不要直接复制其他服务商的 `include` 或 DKIM 记录。

## 验证

至少完成以下测试：

- 向 Gmail、Outlook、QQ 邮箱等不同收件系统发送纯文本和 HTML 邮件。
- 检查邮件原始头中的 `Received`、`Authentication-Results`、SPF、DKIM 和 DMARC 结果。
- 验证不存在 Relay 拒绝、配额耗尽、TLS 降级或身份验证失败。
- 使用邮件鉴权测试工具检查配置，但不要把单一评分当作送达保证。
- 验证退信能够回到受监控的邮箱，并建立告警或人工处理流程。

如果 Stalwart 已接受邮件但外部收件箱没有收到，应同时检查 Stalwart 队列、Relay 事件日志和收件方退信，而不是仅重复发送。
