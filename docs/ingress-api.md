# Ingress API

Ingress 提供邮件接收、健康检查、桌面端账号创建和邮件管理接口。除健康检查外，接口必须通过 OpenResty 的 HTTPS 入口访问。

## 鉴权

项目使用两类独立凭据：

| 凭据 | 请求头 | 用途 |
| :--- | :--- | :--- |
| Ingress 密钥（`INGRESS_SECRET_KEY`） | `X-Ingress-Secret` | Worker 投递、邮件查询与管理 |
| 桌面端注册码（`ACCOUNT_REGISTRATION_CODE`） | `X-Registration-Code` | 创建普通邮箱账号 |

两个值都至少需要 32 个字符，不能混用。不得将凭据写入 URL、日志或安装包。

v2 邮件接口只接受请求头中的 Ingress 密钥。兼容接口仍接受 `?secret=` 查询参数，供旧调用方迁移；新代码不得使用该方式。

## 接口清单

| 方法 | 路径 | 鉴权 | 用途 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/email-ingress/health` | 无 | 健康状态与注册功能状态 |
| `POST` | `/api/email-ingress` | Ingress 密钥 | Worker 投递邮件 |
| `POST` | `/api/email-ingress/v2/accounts` | 桌面端注册码 | 创建普通邮箱账号 |
| `GET` | `/api/email-ingress/v2/messages` | Ingress 密钥 | 游标分页查询摘要 |
| `GET` | `/api/email-ingress/v2/messages/{id}` | Ingress 密钥 | 获取正文、链接、验证码和附件元数据 |
| `PATCH` | `/api/email-ingress/v2/messages/{id}` | Ingress 密钥 | 更新 `is_read` |
| `DELETE` | `/api/email-ingress/v2/messages/{id}` | Ingress 密钥 | 删除单封邮件 |
| `DELETE` | `/api/email-ingress/v2/messages?to={address}` | Ingress 密钥 | 删除指定收件地址的邮件 |
| `GET` | `/api/email-ingress/v2/messages/{id}/raw` | Ingress 密钥 | 下载原始 `.eml` |
| `GET` | `/api/email-ingress/v2/messages/{id}/attachments/{part}` | Ingress 密钥 | 下载附件 |

## 邮件接收语义

`POST /api/email-ingress` 接受：

```json
{
  "from": "sender@example.com",
  "to": "user@onprs.online",
  "raw": "RFC 822 邮件文本"
}
```

Ingress 先解析原始邮件；无法解析时返回 `400` 和 `invalid_raw_email`。只有在全部收件记录保存成功后才会继续处理，持久化失败时返回 `500` 和 `message_persistence_failed`。随后服务尝试通过内部 SMTP 投递至 Stalwart。内部 SMTP 失败时接口仍返回 `200`，此时邮件保留在 Ingress SQLite 中。调用方不能把 `200` 解释为 Stalwart 已完成投递。

## 分页查询

`GET /api/email-ingress/v2/messages` 支持：

- `to`：完整收件地址。
- `q`：搜索发件人、收件人、主题和纯文本正文。
- `since`：Unix 秒时间戳。
- `cursor`：上一页返回的 `next_cursor`。
- `limit`：每页 `1` 至 `100` 条，默认 `50`。

```bash
curl --fail \
  -H "X-Ingress-Secret: $INGRESS_SECRET_KEY" \
  "https://mail.onprs.online/api/email-ingress/v2/messages?limit=50"
```

列表只返回摘要。详情接口会将该邮件标记为已读，并按需解析附件元数据。列表响应中的 `recipients` 提供最近收件地址、邮件数量和最近收件时间。

## 创建邮箱

创建接口只接受 `local_part`、`password` 和可选的 `display_name`：

```json
{
  "local_part": "user.name",
  "password": "至少十二个字符的密码",
  "display_name": "显示名称"
}
```

服务端固定使用已配置的 `MAIL_DOMAIN`、普通用户角色和预先解析的 Stalwart 域标识。客户端不能指定角色、权限、域名或 Stalwart 地址。

客户端应根据 HTTP 状态和稳定机器码处理结果，不应匹配可读错误文本：

| HTTP 状态 | 机器码 | 含义 |
| :--- | :--- | :--- |
| `401` | `registration_code_invalid` | 注册码不匹配 |
| `503` | `registration_not_configured` | 服务端注册配置不完整 |
| `503` | `registration_commit_unknown` | 请求结果未知，先尝试登录或人工核对 |
| `503` | `provisioning_unavailable` | Stalwart 配置服务暂不可用 |

`409` 表示邮箱地址已存在，`422` 表示邮箱名、密码或显示名称不符合约束。完整部署步骤见 [桌面端账号创建](account-registration.md)。

## 兼容接口

`GET` 和 `DELETE /api/email-ingress/messages` 为兼容接口，会返回完整正文并允许查询参数密钥。该接口仅用于已有调用方，后续功能只在 v2 中扩展。
