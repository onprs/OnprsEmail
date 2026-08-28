# Cloudflare Email Worker 配置

Cloudflare Email Routing 接收发往 `@onprs.online` 的邮件，并通过 Email Worker 将邮件转发到 Ingress HTTPS 接口。该链路用于绕过服务器公网 TCP 25 入站限制。

## 数据流与边界

Worker 将发件人、收件人和 RFC 822 邮件内容组成 JSON 请求，并使用 Ingress 密钥鉴权。Ingress 收到请求后先写入 SQLite，再尝试通过内部 SMTP 投递至 Stalwart。

当前 Worker 使用 UTF-8 文本序列化原始邮件，不应表述为逐字节无损传输。上线前需要用实际业务中的非 ASCII 正文、常见附件和接近大小上限的邮件验证兼容性。

## 创建 Worker

1. 在 Cloudflare 控制台进入 **Workers & Pages**。
2. 创建一个 Worker，并使用仓库中的 `cloudflare-worker/worker.js` 替换默认代码。
3. 部署 Worker。

Worker 名称只用于 Cloudflare 控制台识别，不影响服务端接口。

## 配置变量

在 Worker 的 **Settings > Variables and Secrets** 中配置：

| 名称 | 类型 | 值 | 说明 |
| :--- | :--- | :--- | :--- |
| `INGRESS_URL` | 普通变量 | `https://mail.onprs.online/api/email-ingress` | Ingress 公开接收地址 |
| `INGRESS_SECRET` | Secret | 与服务器 `.env` 的 `INGRESS_SECRET_KEY` 相同 | Ingress 密钥 |

`INGRESS_SECRET` 不得保存为普通明文变量、写入代码或输出到日志。轮换密钥时，服务端与 Worker 需要协调更新；两端值不一致期间邮件会被拒绝。

## 启用 Catch-all

1. 在 `onprs.online` 的 Cloudflare 控制台进入 **Email > Email Routing**。
2. 确认 Email Routing 已启用，且根域 MX 为 Cloudflare 自动生成的三条记录。
3. 编辑 Catch-all 规则，将操作设为 **Send to Worker**。
4. 选择刚部署的 Worker 并启用规则。

不要把根域 MX 改为 `mail.onprs.online`。完整记录见 [DNS 配置](dns-setup.md)。

## 验证

1. 从域名外部的邮箱向一个已存在的 `@onprs.online` 账号发送测试邮件。
2. 在 Cloudflare Worker 日志中确认请求获得 2xx 响应，且日志中没有响应体异常。
3. 使用 Ingress v2 API 确认 SQLite 中存在该邮件。
4. 使用 SnappyMail 或 IMAP 确认 Stalwart 中也存在该邮件。
5. 使用带附件和非 ASCII 主题的测试邮件重复验证。

Worker 获得 2xx 只表示 Ingress 已接受并处理请求，不单独证明内部 SMTP 投递成功。若邮件只出现在 Ingress API 中，应检查 `email-ingress-gateway` 日志和 Stalwart SMTP 状态。

## 故障处理

- `401`：核对 Worker `INGRESS_SECRET` 与服务器 `INGRESS_SECRET_KEY`。
- `400`：检查请求大小和 Worker 生成的 JSON 字段。
- `5xx`：检查 Ingress 容器、SQLite 数据目录和反向代理日志。
- Worker 网络异常：确认 `INGRESS_URL` 的证书、DNS 和 Cloudflare 到源站的 HTTPS 连通性。

处理期间不要在工单、聊天记录或公开日志中粘贴密钥和完整真实邮件。
