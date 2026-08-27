# Onprs Email 出站 SMTP Relay（中继）配置指南

## 1. 为什么需要 SMTP Relay？

在主机环境实测中已核实：云服务商（Bytevirt VPS）**封禁了出站的 TCP 25 端口**。这意味着本机 MTA 无法通过 25 端口直连外部目标（如 Gmail、Outlook、QQ 邮箱等）的接收服务器。

因此，所有发往外部公共邮箱的邮件，需要通过开放的 **587 (STARTTLS)** 或 **465 (SSL/TLS)** 端口，经由受信任的 SMTP Relay 中继服务商代理投递。

---

## 2. 常见免费/高送达率 Relay 服务商推荐

1. **Brevo (原 Sendinblue)**：
   - 免费额度：每日 300 封邮件，永久免费。
   - SMTP 主机：`smtp-relay.brevo.com`，端口 `587`。
2. **Resend**：
   - 免费额度：每月 3,000 封邮件（每日限额 100 封）。
   - 开发者体验好，API 与 SMTP 双支持。
   - SMTP 主机：`smtp.resend.com`，端口 `465` 或 `587`。
3. **Amazon SES (Simple Email Service)**：
   - 极低成本，送达率行业顶尖，每万封仅约 $0.10。
4. **自建中继（备选）**：
   - 使用其他 25 端口开放的海外 VPS 搭建轻量 Postfix 作为专有转发中继。

---

## 3. 在 Stalwart Mail Server 中配置 Outbound Relay

Stalwart 原生支持多维度出站路由规则（Outbound Routing）。

### 3.1 步骤说明（WebUI 管理后台）

1. 登录 Stalwart 管理后台；
2. 导航至 **Settings** -> **Routing** -> **Outbound**；
3. 新建或编辑默认出站规则（Default Routing Rule）：
   - **Protocol**: `SMTP`
   - **Host**: 填写中继服务商主机名（如 `smtp-relay.brevo.com`）
   - **Port**: `587` (STARTTLS) 或 `465` (Implicit TLS)
   - **Authentication**: `Username & Password`
   - **Username**: 填写中继服务商提供的 SMTP 登录用户名
   - **Password**: 填写中继服务商生成的 SMTP 专用密码/API Key
4. 保存并测试发信。

---

## 4. 发信送达率测试（Mail-Tester）

完成 DNS 与 Relay 设置后，访问 [mail-tester.com](https://www.mail-tester.com/)：
1. 复制 Mail-Tester 提供的临时邮箱地址；
2. 从你的域名邮箱（例如 `admin@onprs.online`）向该测试地址发送一封正常带正文的邮件；
3. 在 Mail-Tester 刷新查看评分（目标得分：**9/10 ~ 10/10**）；
4. 根据报告查漏补缺（如 DKIM、SPF 匹配度、HTML 正文格式等）。
