# Cloudflare Email Worker (Catch-all 通配规则) 配置指南

本方案使用 Cloudflare Email Routing 的 **Catch-all（全域通配规则）** 配合 **Email Worker**，将发往 `*@onprs.online` 的所有邮件自动、无损、秒级直投到自建 Stalwart 邮件服务器中。

---

## 🌟 核心优势

- **一次配置，终身通用**：无需为每个新邮箱单独配置转发规则；任何新建账号（如 `admin@`、`onprs@`、`service@` 等）均自动生效。
- **免 25 端口依赖**：彻底摆脱 VPS 厂商拦截 25 端口的限制。
- **云端原生防护**：自动享受 Cloudflare 提供的 DDoS 防护与反垃圾邮件第一道过滤。

---

## 📋 三步完成通用配置

### 第一步：创建 Cloudflare Email Worker

1. 登录 Cloudflare 控制台 -> 点击左侧导航栏 **Workers & Pages** -> **Create application** -> **Create Worker**；
2. 为 Worker 命名（例如 `onprs-email-worker`），点击 **Deploy（部署）**；
3. 点击 **Edit code（编辑代码）**，将本仓库 `cloudflare-worker/worker.js` 中的全部代码粘贴进去；
4. 点击 **Save and deploy（保存并部署）**。

---

### 第二步：配置 Worker 环境变量（Settings -> Variables）

在刚创建的 Worker 详情页面，点击 **Settings** -> **Variables and Secrets**，添加以下环境变量：

| 变量名 (Variable Name) | 变量值 (Value) | 说明 |
| :--- | :--- | :--- |
| **`INGRESS_URL`** | `https://mail.onprs.online/api/email-ingress` | 服务器通用接收接口地址 |
| **`INGRESS_SECRET`** | `<服务器 .env 中的 INGRESS_SECRET_KEY>` | 使用 Cloudflare Secret 保存，不要作为明文变量公开 |

---

### 第三步：在域名中启用 Catch-all 规则

1. 进入 Cloudflare 控制台 -> 选择你的域名 **`onprs.online`**；
2. 左侧菜单点击 **Email（电子邮件）** -> **Email Routing（电子邮件路由）**；
3. 进入 **Routing rules（路由规则）** 标签页：
   - 找到 **Catch-all rule（通配所有地址规则）**；
   - 点击 **Edit（编辑）**；
   - **Action（操作）**：选择 **Send to Worker（发送至 Worker）**；
   - **Destination（目标）**：选择第一步创建的 `onprs-email-worker`；
   - 状态切换为 **Active（已启用）** 并保存。
4. 切换到 **Overview** 页面，确认 Cloudflare 自动添加的 MX 解析记录已激活。

---

## 🚀 验证测试

完成上述配置后：
使用外部邮箱（如 QQ / 163 / Gmail）向 `*@onprs.online` 下的**任意已创建邮箱账号**发送一封邮件，邮件将瞬间直达你的 Stalwart 数据库并在 SnappyMail / IMAP 客户端中呈现！
