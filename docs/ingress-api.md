# Ingress 查询 API

Ingress v2 为本地桌面客户端提供摘要分页、邮件详情和附件下载，同时保留原有 v1 接口。

## 鉴权

v2 只接受请求头中的共享密钥，不接受 URL 查询参数：

```http
X-Ingress-Secret: <INGRESS_SECRET_KEY>
```

密钥至少需要 32 个字符，必须通过 HTTPS 传输，并由客户端安全存储。不要将密钥写入日志、URL 或安装包。

## 接口

| 方法 | 路径 | 用途 |
| :--- | :--- | :--- |
| `GET` | `/api/email-ingress/health` | 无鉴权健康检查 |
| `POST` | `/api/email-ingress/v2/accounts` | 使用独立注册码创建普通邮箱账号 |
| `GET` | `/api/email-ingress/v2/messages` | 游标分页查询摘要 |
| `GET` | `/api/email-ingress/v2/messages/{id}` | 获取正文、链接、验证码和附件元数据 |
| `PATCH` | `/api/email-ingress/v2/messages/{id}` | 更新 `is_read` 状态 |
| `DELETE` | `/api/email-ingress/v2/messages/{id}` | 删除单封邮件 |
| `DELETE` | `/api/email-ingress/v2/messages?to={address}` | 清理指定收件地址 |
| `GET` | `/api/email-ingress/v2/messages/{id}/raw` | 下载原始 `.eml` |
| `GET` | `/api/email-ingress/v2/messages/{id}/attachments/{part}` | 下载附件 |

## 邮箱创建

邮箱创建接口使用独立请求头，不接受 Ingress 查询密钥：

```http
X-Registration-Code: <ACCOUNT_REGISTRATION_CODE>
```

请求只接受 `local_part`、`password` 和 `display_name`，服务端使用预先核对的固定域标识和普通用户角色。Stalwart 受限配置令牌只保存在服务器端。桌面客户端不会接受自定义注册 Origin。注册码不匹配时返回 HTTP `401` 和稳定机器码 `registration_code_invalid`；客户端只能依据该机器码使参与本次请求的本地保存值失效，普通代理层 `401/403` 不代表注册码已失效。完整配置步骤见 [account-registration.md](account-registration.md)。

## 分页查询

支持的参数：

- `to`：完整收件地址。
- `q`：搜索发件人、收件人、主题和纯文本正文。
- `since`：Unix 秒时间戳。
- `cursor`：上一页返回的 `next_cursor`。
- `limit`：每页数量，范围 `1-100`，默认 `50`。

```bash
curl -H "X-Ingress-Secret: $INGRESS_SECRET_KEY" \
  "https://mail.onprs.online/api/email-ingress/v2/messages?limit=50"
```

列表接口只返回摘要，正文和附件元数据由详情接口按需获取。响应中的 `recipients` 提供最近收件地址及邮件数量。

## 兼容性

原有 `/api/email-ingress/messages` GET/DELETE 接口保持可用。旧接口仍允许查询参数密钥以兼容已有调用方，新客户端必须使用 v2 请求头鉴权。
