# 备份与恢复

## 备份范围

`scripts/backup.sh` 生成带 SHA-256 校验文件的 `tar.gz` 归档。默认保留 14 天，可通过 `BACKUP_RETENTION_DAYS` 调整。

| 内容 | 备份方式 | 一致性说明 |
| :--- | :--- | :--- |
| Ingress SQLite | Python `sqlite3.Connection.backup()` | 在线一致性快照，并执行 `PRAGMA integrity_check` |
| PostgreSQL | 容器内执行 `pg_dump --format=custom` | 数据库逻辑快照 |
| Stalwart 配置与本地数据 | 直接读取持久化目录 | 文件级备份；频繁变更配置时应安排维护窗口 |
| SnappyMail 数据 | 复制宿主机根数据目录；旧部署未迁移时从容器复制 `/var/lib/snappymail` | 归档统一保存为 `snappymail-data/`；清单记录来源为 `host` 或 `container` |
| `.env` 与 Compose 配置 | 复制到归档 `metadata/` | 包含恢复所需配置，也包含敏感凭据 |

项目使用 PostgreSQL 时，`POSTGRES_BACKUP_ENABLED` 必须保持为 `true`，并填写容器名、数据库名和备份用户。参数缺失、容器未运行或 `pg_dump` 失败时，脚本会返回非零退出码且不发布归档。

脚本会核对运行中 SnappyMail 容器的 `/var/lib/snappymail` 挂载来源。若旧部署仍使用镜像自动创建的匿名卷，脚本会从容器复制真实运行数据，而不是备份空的宿主机子目录。完成新版部署迁移后，数据来源应变为 `host`。

仅在确定 Stalwart 不使用 PostgreSQL 时，才可显式设置：

```dotenv
POSTGRES_BACKUP_ENABLED=false
```

此时脚本会输出警告，归档清单中的 `postgresql_included` 为 `false`。

## 创建备份

```bash
bash scripts/backup.sh
```

脚本先在 `backups/` 中创建临时文件，完成 SQLite 校验、PostgreSQL 导出和压缩包目录校验后，再原子移动为最终归档。只有新归档与校验文件都成功生成后，才清理超过保留期的旧备份。

## 校验备份

```bash
cd backups
sha256sum --check onprs_email_backup_YYYYMMDD_HHMMSS.tar.gz.sha256
tar -tzf onprs_email_backup_YYYYMMDD_HHMMSS.tar.gz
```

归档至少应包含以下内容：

```text
metadata/environment.env
metadata/docker-compose.yml
metadata/manifest.txt
ingress-data/ingress_emails.db
snappymail-data/_data_/
postgresql/<数据库名>.dump
```

尚未收到邮件时，Ingress SQLite 文件可能不存在。PostgreSQL 备份启用时，数据库导出文件必须存在且非空。`metadata/manifest.txt` 中的 `snappymail_source` 用于确认 SnappyMail 数据来自宿主机还是旧容器。

## 安全要求

备份包含真实邮件、密码哈希、令牌、注册码和可能存在的 DKIM 私钥。必须遵守以下要求：

- 备份目录和归档保持仅部署用户可读。
- 不得将归档、校验文件或解压目录加入 Git。
- 复制到对象存储、个人电脑或其他服务器前，先使用受控密钥加密。
- 加密密钥与备份文件分开保存。
- 定期验证异地副本可下载、可解密且校验通过。

SHA-256 只能发现损坏，不能提供机密性或来源认证。

## 恢复演练

恢复操作具有破坏性，本仓库不提供自动覆盖生产目录的脚本。至少每季度在隔离环境执行一次恢复演练：

1. 校验归档 SHA-256，并将归档解压到空目录。
2. 阅读 `metadata/manifest.txt`，核对备份时间、来源主机、原路径和 PostgreSQL 是否包含。
3. 使用隔离的 PostgreSQL 实例执行 `pg_restore --list`，再恢复到空数据库。
4. 对 `ingress-data/ingress_emails.db` 执行 `PRAGMA integrity_check`。
5. 使用隔离的数据目录和端口启动 Stalwart、Ingress 与 SnappyMail。
6. 验证管理员登录、IMAP 登录、Ingress 列表与详情、附件下载和测试邮件收发。
7. 记录恢复耗时、缺失配置和人工步骤，并据此修订运行手册。

生产恢复前应停止相关写入，另行备份当前故障现场，并明确数据库和文件目录的回退点。不要直接在运行中的生产目录上试验恢复命令。
