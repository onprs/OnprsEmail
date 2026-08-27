# 桌面端创建邮箱

Onprs Mail Desktop 可以直接创建 `@onprs.online` 邮箱，不需要打开 Stalwart 网页后台。账号创建由 Ingress 代理完成，桌面端不会接触或保存 Stalwart 管理凭据。

## 1. 部署基础服务

首次部署先执行：

```bash
bash scripts/setup.sh
```

脚本会生成独立的 `ACCOUNT_REGISTRATION_CODE`，但不会把秘密值打印到普通部署输出。`.env` 会被设为 `0600`，注册码只在需要录入客户端时由服务器部署用户查看。

## 2. 创建受限配置主体

在服务器项目目录执行：

```bash
python3 scripts/configure-registration.py
```

脚本会临时提示输入 Stalwart 管理员账号和密码，并与 `setup.sh` 共用项目级排他锁，防止部署和令牌轮换并发交错。随后完成以下操作：

- 解析并核对 `.env` 中 `MAIL_DOMAIN` 对应的 Stalwart 域标识。
- 创建无密码的 `desktop-registration@onprs.online` 专用普通用户。
- 让该用户继承普通 `User` 权限，只额外授予 `sysAccountCreate`。
- 生成一个继承专用主体权限的新 API Key。
- 将域标识和新令牌原子写入权限为 `0600` 的 `.env`。
- 显式重建 Ingress，并等待健康检查确认新令牌已生效。
- 只撤销带有本脚本描述标记的旧 API Key；写入或激活失败时恢复旧配置并撤销新 Key。

Stalwart 0.16.18 在创建普通 `User` 时会校验调用者能否授予该用户继承到的全部权限。因此，不能把只有 `sysAccountCreate` 的 API Key 直接挂在管理员账号上。专用普通用户既满足该授权检查，也将邮件读取能力隔离在一个无密码、无业务邮件的服务账号内；额外管理权限仍只有 `sysAccountCreate`。

管理员密码只存在于脚本进程内存中，不会写入 `.env`。再次运行脚本会轮换 API Key，但默认保留现有注册码。需要同时轮换注册码时执行：

```bash
python3 scripts/configure-registration.py --rotate-registration-code
```

## 3. 确认注册服务

配置脚本会自动执行带构建的 Ingress 重建。完成后可再次确认：

```bash
curl https://mail.onprs.online/api/email-ingress/health
```

健康响应中的 `registration_enabled` 应为 `true`。

## 4. 在桌面端创建邮箱

打开 Onprs Mail，选择“创建邮箱”，首次填写邮箱名、显示名称、密码和服务器 `.env` 中的 `ACCOUNT_REGISTRATION_CODE`。客户端的注册目标固定为 `https://mail.onprs.online`，创建成功后会自动登录。默认情况下，已由服务端明确确认成功的注册码会通过 Electron `safeStorage` 使用 Windows DPAPI 加密后保存在本机；以后可直接创建其他邮箱，也可在创建页更换或忘记本机注册码。网络中断后的恢复登录不视为注册码已确认，因此不会保存未经确认的候选值。

## 安全边界

- 注册码和 Ingress 查询密钥相互独立，不能混用。
- 注册码只随创建请求通过 HTTPS 发送；用户选择记住时，仅在服务端明确确认创建成功后以 Windows DPAPI 密文存入独立本地记录，明文不会写入 SQLite、设置 JSON 或返回 Renderer。
- Stalwart 配置令牌只存在于服务器 `.env` 和 Ingress 进程环境中，不进入安装包。
- 服务端固定创建普通 `User`，客户端不能指定角色、权限、域名或 Stalwart 地址。
- `admin`、`postmaster`、`abuse`、`desktop-registration` 等系统地址不能通过桌面端注册。
- 注册接口按来源地址限制请求频率。
- Stalwart 与 SnappyMail 镜像按生产环境实际运行 digest 固定，升级前必须重新验证注册契约。

`allowedIps` 需要以 Stalwart 实际观察到的 Ingress 来源地址为准。当前工作区没有 Docker，尚未验证该地址，因此配置脚本不会凭假设写入 IP 限制。部署后可先核对容器网络，再为专用 API Key 增加经过验证的来源限制。

需要停用创建功能时，清空服务器 `.env` 中的 `ACCOUNT_REGISTRATION_CODE`、`STALWART_PROVISIONING_TOKEN` 或 `STALWART_REGISTRATION_DOMAIN_ID`，然后重新创建 `email-ingress` 容器。
