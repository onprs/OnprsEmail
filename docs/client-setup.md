# 客户端连接与反向代理

## 邮件客户端参数

账号名统一填写完整邮箱地址，例如 `user@onprs.online`。

### 发送邮件

| 项目 | 推荐值 | 备选值 |
| :--- | :--- | :--- |
| 主机 | `mail.onprs.online` | 无 |
| 端口 | `465` | `587` |
| 加密 | 隐式 TLS（SMTPS） | STARTTLS（Submission） |
| 身份验证 | 必须 | 必须 |

### 接收邮件

| 项目 | 推荐值 | 备选值 |
| :--- | :--- | :--- |
| 主机 | `mail.onprs.online` | 无 |
| 协议 | IMAP | POP3 |
| 端口 | `993`（隐式 TLS） | `995`（隐式 TLS） |
| 身份验证 | 必须 | 必须 |

`143`、`110` 只应在客户端和服务端均明确启用 STARTTLS 时使用。不要通过未加密连接发送账号密码。

## OpenResty 反向代理

1Panel OpenResty 负责公网 HTTPS 终止，Compose 中的三个 HTTP 服务只绑定到 `127.0.0.1`。

| 公网域名或路径 | 本机上游 | 服务 |
| :--- | :--- | :--- |
| `https://mail.onprs.online` | `http://127.0.0.1:4080` | Stalwart 管理与 JMAP |
| `https://mail.onprs.online/api/email-ingress` | `http://127.0.0.1:4082` | Ingress API |
| `https://use-mail.onprs.online` | `http://127.0.0.1:4081` | SnappyMail |

为两个站点配置有效证书和 HTTP 到 HTTPS 跳转。Ingress 路径代理必须保留请求方法、请求体以及 `X-Ingress-Secret`、`X-Registration-Code` 请求头，并允许符合邮件上限的请求体大小。

在 Stalwart 中启用对反向代理头的支持前，先确认 OpenResty 会覆盖而不是透传客户端伪造的 `X-Forwarded-For`。可信代理网段必须来自实际 Docker 网络，不要复制固定示例网段：

```bash
docker network inspect 1panel-network
docker network ls
docker network inspect <本项目的 mail-network 实际名称>
```

根据命令输出配置 Stalwart 的可信来源和允许规则。Compose 项目名、网络创建顺序或 1Panel 配置变化后，Docker 网段可能改变。

## 邮件协议 TLS

Stalwart 可以自行通过 ACME 管理证书，也可以读取 Compose 只读挂载的 1Panel 证书目录：

```text
宿主机：/opt/1panel/apps/openresty/openresty/conf/ssl
容器内：/etc/ssl/1panel
```

配置证书路径后，分别验证 465、587、993 和 143 的证书链、主机名与协议模式。HTTPS 页面正常不代表 SMTP 或 IMAP 已使用同一张有效证书。

SnappyMail 容器通过内部 Docker 网络连接 Stalwart。`setup.sh` 会把仓库中的域模板写入新数据目录；模板允许内部自签名证书且关闭证书校验。公网客户端仍必须校验 `mail.onprs.online` 的证书。若内部链路已部署受信任证书，应同步收紧 [SnappyMail 域配置模板](../config/snappymail/domains/onprs.online.json)，并在已部署的数据目录中更新对应域配置。

## 验证

```bash
bash scripts/test-email.sh
openssl s_client -connect mail.onprs.online:465 -servername mail.onprs.online
openssl s_client -connect mail.onprs.online:993 -servername mail.onprs.online
openssl s_client -starttls smtp -connect mail.onprs.online:587 -servername mail.onprs.online
```

最后使用普通邮箱账号完成一次登录、收信和发信测试。管理账号不应作为日常客户端测试账号。
