# 桌面端账号创建

Onprs Mail Desktop 通过 Ingress 创建 `@onprs.online` 普通邮箱账号，不需要向客户端提供 Stalwart 管理员凭据。

## 部署基础服务

```bash
bash scripts/setup.sh
```

首次运行会生成独立的 `ACCOUNT_REGISTRATION_CODE`，但不会在部署输出中显示具体值。`.env` 权限设置为 `0600`，只有部署用户可以读取。

## 创建专用服务账号

```bash
python3 scripts/configure-registration.py
```

脚本临时读取 Stalwart 管理员账号和密码，并与 `setup.sh` 共用项目级排他锁。它会执行以下操作：

- 解析并核对 `MAIL_DOMAIN` 对应的 Stalwart 域标识。
- 创建无密码的 `desktop-registration@onprs.online` 专用普通用户。
- 让该用户继承普通用户权限，只额外授予 `sysAccountCreate`。
- 创建继承该用户权限的受限配置令牌。
- 将域标识和令牌原子写入权限为 `0600` 的 `.env`。
- 重建 Ingress，并等待健康端点确认注册功能已启用。
- 仅撤销带有本脚本描述标记的旧令牌；写入或激活失败时恢复旧配置。

Stalwart 0.16.18 在创建普通用户时，会校验调用者是否有权授予该用户继承到的全部权限。因此，受限令牌由一个无密码、无业务邮件的专用普通用户持有，而不是直接挂在管理员账号上。该账号额外获得的管理权限只有 `sysAccountCreate`。

管理员密码只存在于脚本进程内存中，不写入 `.env`。再次运行脚本会轮换受限配置令牌，但默认保留现有注册码。同时轮换注册码时执行：

```bash
python3 scripts/configure-registration.py --rotate-registration-code
```

## 验证

```bash
curl --fail https://mail.onprs.online/api/email-ingress/health
```

响应中的 `registration_enabled` 应为 `true`。随后使用测试邮箱名完成一次创建和登录，确认账号位于固定域名下且角色为普通用户。

## 客户端行为

用户在 Onprs Mail 的“创建邮箱”页面填写邮箱名、显示名称、密码和注册码。客户端注册目标固定为 `https://mail.onprs.online`。

只有服务端明确确认创建成功后，客户端才可将注册码视为有效。用户选择记住注册码时，Windows 客户端通过 Electron `safeStorage` 使用 DPAPI 加密保存；明文不写入 SQLite、设置 JSON 或 Renderer 可访问的数据。网络中断或 `registration_commit_unknown` 不能视为注册码已确认，此时应先尝试登录或由管理员核对账号是否已经创建。

## 安全边界

- 注册码与 Ingress 密钥相互独立。
- 注册码只通过 HTTPS 创建请求发送。
- 受限配置令牌只存在于服务器 `.env` 和 Ingress 进程环境中。
- 服务端固定创建普通用户；客户端不能指定角色、权限、域名或 Stalwart 地址。
- `admin`、`postmaster`、`abuse`、`desktop-registration` 等保留地址不能注册。
- 注册接口按来源地址限制请求频率。
- 镜像升级前必须重新验证 JMAP 会话、账号创建和令牌轮换契约。

API Key 的来源地址限制必须基于 Stalwart 实际观察到的 Ingress 容器地址。先通过 `docker network inspect` 核对当前网络，再配置 `allowedIps`；不要复制其他环境的固定网段。

## 停用

清空 `.env` 中以下任一值，然后重建 `email-ingress`：

- `ACCOUNT_REGISTRATION_CODE`
- `STALWART_PROVISIONING_TOKEN`
- `STALWART_REGISTRATION_DOMAIN_ID`

```bash
docker compose up -d --force-recreate --no-deps email-ingress
```

停用后再次检查健康响应，`registration_enabled` 应为 `false`。
