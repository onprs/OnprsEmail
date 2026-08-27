#!/usr/bin/env python3
"""为桌面端邮箱注册创建隔离的 Stalwart 配置主体和 API Key。"""

import argparse
import base64
import getpass
import http.client
import json
import os
import secrets
import subprocess
import sys
import time
try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt
from pathlib import Path
from urllib.parse import urlparse

CORE = "urn:ietf:params:jmap:core"
STALWART = "urn:stalwart:jmap"
SERVICE_ACCOUNT_LOCAL_PART = "desktop-registration"
SERVICE_ACCOUNT_DESCRIPTION = "Onprs Mail desktop registration service"
API_KEY_DESCRIPTION = "Onprs Mail desktop registration provisioning"
SERVICE_ACCOUNT_PERMISSIONS = {"sysAccountCreate": True}
PROJECT_LOCK_FILE = ".registration.lock"
project_lock_handle = None


def configure_utf8_output(streams=None):
    """确保中文运维输出不受宿主机缺失 UTF-8 locale 影响。"""
    targets = streams if streams is not None else (sys.stdout, sys.stderr)
    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            continue


def hold_project_lock(project_root: Path):
    """持有跨进程部署锁直到脚本退出，避免令牌轮换与容器重建交错。"""
    global project_lock_handle
    if project_lock_handle is not None:
        return
    lock_path = project_root / PROJECT_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b", buffering=0)
    os.chmod(lock_path, 0o600)
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    else:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    project_lock_handle = handle


def release_project_lock():
    global project_lock_handle
    handle = project_lock_handle
    if handle is None:
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()
    project_lock_handle = None


def request_json(url: str, method: str, authorization: str, payload=None):
    target = urlparse(url)
    local_http = target.scheme == "http" and target.hostname in ("localhost", "127.0.0.1")
    if target.scheme != "https" and not local_http:
        raise RuntimeError("Stalwart 地址必须使用 HTTPS")
    if not target.hostname or target.username or target.password:
        raise RuntimeError("Stalwart 地址格式无效")

    connection_class = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(target.hostname, target.port or (443 if target.scheme == "https" else 80), timeout=30)
    path = target.path or "/"
    if target.query:
        path = f"{path}?{target.query}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "Authorization": authorization}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(5 * 1024 * 1024 + 1)
    finally:
        connection.close()
    if response.status in (301, 302, 303, 307, 308):
        raise RuntimeError("Stalwart 返回了重定向，已拒绝继续发送管理员凭据")
    if len(data) > 5 * 1024 * 1024:
        raise RuntimeError("Stalwart 返回数据过大")
    try:
        parsed = json.loads(data.decode("utf-8")) if data else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Stalwart 返回了无法解析的数据") from error
    if response.status != 200:
        detail = parsed.get("detail") if isinstance(parsed, dict) else None
        raise RuntimeError(detail or f"Stalwart 请求失败（HTTP {response.status}）")
    if not isinstance(parsed, dict):
        raise RuntimeError("Stalwart 返回数据格式无效")
    return parsed


def get_jmap_session(server: str, authorization: str):
    """直接读取 Stalwart 会话端点，避免携带凭据处理发现重定向。"""
    return request_json(f"{server}/jmap/session", "GET", authorization)


def jmap_call(api_url: str, authorization: str, account_id: str, method_name: str, arguments, call_id: str):
    result = request_json(api_url, "POST", authorization, {
        "using": [CORE, STALWART],
        "methodCalls": [[method_name, {**arguments, "accountId": account_id}, call_id]],
    })
    responses = result.get("methodResponses")
    if not isinstance(responses, list) or not responses:
        raise RuntimeError(f"Stalwart 没有返回 {method_name} 结果")
    response = responses[0]
    if not isinstance(response, list) or len(response) < 2:
        raise RuntimeError(f"Stalwart 返回了无效的 {method_name} 结果")
    response_name = response[0]
    response_data = response[1] if isinstance(response[1], dict) else {}
    if response_name == "error":
        raise RuntimeError(response_data.get("description", f"Stalwart 拒绝执行 {method_name}"))
    if response_name != method_name:
        raise RuntimeError(f"Stalwart 返回了意外的方法结果：{response_name}")
    return response_data


def set_created_id(result, create_id: str, object_name: str) -> str:
    not_created = result.get("notCreated") if isinstance(result, dict) else None
    if isinstance(not_created, dict) and not_created:
        details = not_created.get(create_id, {})
        raise RuntimeError(details.get("description", f"{object_name}创建失败"))
    created = result.get("created", {}).get(create_id, {}) if isinstance(result, dict) else {}
    object_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(object_id, str) or not object_id:
        raise RuntimeError(f"Stalwart 未返回{object_name}标识")
    return object_id


def discover_domain(api_url: str, authorization: str, admin_account_id: str, mail_domain: str) -> str:
    query = jmap_call(api_url, authorization, admin_account_id, "x:Domain/query", {
        "filter": {"name": mail_domain},
        "position": 0,
        "limit": 2,
    }, "query-domain")
    domain_ids = query.get("ids")
    if not isinstance(domain_ids, list) or len(domain_ids) != 1 or not isinstance(domain_ids[0], str):
        raise RuntimeError("邮件域名未配置或存在重复配置")
    domain_id = domain_ids[0]

    result = jmap_call(api_url, authorization, admin_account_id, "x:Domain/get", {
        "ids": [domain_id],
        "properties": ["id", "name", "isEnabled"],
    }, "get-domain")
    domains = result.get("list")
    domain = domains[0] if isinstance(domains, list) and domains else None
    if not isinstance(domain, dict) or domain.get("name") != mail_domain or domain.get("isEnabled") is False:
        raise RuntimeError("邮件域名当前不可用于创建账号")
    return domain_id


def service_account_payload(domain_id: str):
    return {
        "@type": "User",
        "name": SERVICE_ACCOUNT_LOCAL_PART,
        "domainId": domain_id,
        "credentials": {},
        "memberGroupIds": {},
        "roles": {"@type": "User"},
        "permissions": {
            "@type": "Merge",
            "enabledPermissions": SERVICE_ACCOUNT_PERMISSIONS,
            "disabledPermissions": {},
        },
        "quotas": {},
        "aliases": {},
        "description": SERVICE_ACCOUNT_DESCRIPTION,
        "encryptionAtRest": {"@type": "Disabled"},
    }


def ensure_service_account(api_url: str, authorization: str, admin_account_id: str, domain_id: str) -> str:
    query = jmap_call(api_url, authorization, admin_account_id, "x:Account/query", {
        "filter": {"name": SERVICE_ACCOUNT_LOCAL_PART, "domainId": domain_id},
        "position": 0,
        "limit": 2,
    }, "query-service-account")
    account_ids = query.get("ids")
    if not isinstance(account_ids, list) or len(account_ids) > 1:
        raise RuntimeError("专用注册主体查询结果无效")

    if not account_ids:
        create_id = "registration-service"
        result = jmap_call(api_url, authorization, admin_account_id, "x:Account/set", {
            "create": {create_id: service_account_payload(domain_id)},
        }, "create-service-account")
        return set_created_id(result, create_id, "专用注册主体")

    service_account_id = account_ids[0]
    if not isinstance(service_account_id, str) or not service_account_id:
        raise RuntimeError("专用注册主体标识无效")
    result = jmap_call(api_url, authorization, admin_account_id, "x:Account/get", {
        "ids": [service_account_id],
        "properties": ["id", "name", "domainId", "description", "credentials"],
    }, "get-service-account")
    accounts = result.get("list")
    account = accounts[0] if isinstance(accounts, list) and accounts else None
    if (
        not isinstance(account, dict)
        or account.get("name") != SERVICE_ACCOUNT_LOCAL_PART
        or account.get("domainId") != domain_id
        or account.get("description") != SERVICE_ACCOUNT_DESCRIPTION
    ):
        raise RuntimeError("同名账号不是本脚本创建的专用注册主体，已拒绝复用")

    credentials = account.get("credentials", {})
    credential_values = credentials.values() if isinstance(credentials, dict) else credentials
    if not isinstance(credential_values, (list, tuple)) and not hasattr(credential_values, "__iter__"):
        raise RuntimeError("专用注册主体的凭据列表无效")
    for credential in credential_values:
        if not isinstance(credential, dict) or credential.get("@type") != "ApiKey":
            raise RuntimeError("专用注册主体包含非 API Key 凭据，已拒绝继续")

    update = service_account_payload(domain_id)
    update.pop("@type")
    update.pop("name")
    update.pop("domainId")
    update.pop("credentials")
    result = jmap_call(api_url, authorization, admin_account_id, "x:Account/set", {
        "update": {service_account_id: update},
    }, "update-service-account")
    not_updated = result.get("notUpdated")
    if isinstance(not_updated, dict) and not_updated:
        details = not_updated.get(service_account_id, {})
        raise RuntimeError(details.get("description", "无法收紧专用注册主体权限"))
    return service_account_id


def list_registration_api_key_ids(api_url: str, authorization: str, service_account_id: str) -> list[str]:
    existing = jmap_call(api_url, authorization, service_account_id, "x:ApiKey/get", {
        "ids": None,
        "properties": ["id", "description"],
    }, "list-registration-keys")
    api_keys = existing.get("list")
    if not isinstance(api_keys, list):
        raise RuntimeError("Stalwart 返回的 API Key 列表无效")

    key_ids = []
    for item in api_keys:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise RuntimeError("Stalwart 返回了无效的 API Key 标识")
        if item.get("description") == API_KEY_DESCRIPTION:
            key_ids.append(item["id"])
    return key_ids


def create_registration_api_key(api_url: str, authorization: str, service_account_id: str):
    create_id = "registration-key"
    result = jmap_call(api_url, authorization, service_account_id, "x:ApiKey/set", {
        "create": {
            create_id: {
                "description": API_KEY_DESCRIPTION,
                "permissions": {"@type": "Inherit"},
                "allowedIps": {},
            }
        },
    }, "create-registration-key")
    not_created = result.get("notCreated") if isinstance(result, dict) else None
    if isinstance(not_created, dict) and not_created:
        details = not_created.get(create_id, {})
        raise RuntimeError(details.get("description", "API Key 创建失败"))
    created = result.get("created", {}).get(create_id, {}) if isinstance(result, dict) else {}
    key_id = created.get("id") if isinstance(created, dict) else None
    token = created.get("secret") if isinstance(created, dict) else None
    if not isinstance(key_id, str) or not key_id:
        raise RuntimeError("Stalwart 未返回新 API Key 标识")
    if not isinstance(token, str) or not token or token == "*****":
        raise RuntimeError("Stalwart 未返回新 API Key 的明文密钥")
    return key_id, token


def destroy_registration_api_keys(
    api_url: str,
    authorization: str,
    service_account_id: str,
    key_ids: list[str],
):
    if not key_ids:
        return
    destroyed = jmap_call(api_url, authorization, service_account_id, "x:ApiKey/set", {
        "destroy": key_ids,
    }, "destroy-registration-keys")
    not_destroyed = destroyed.get("notDestroyed")
    if isinstance(not_destroyed, dict) and not_destroyed:
        raise RuntimeError("无法撤销专用注册主体的 API Key")
    destroyed_ids = destroyed.get("destroyed")
    if not isinstance(destroyed_ids, list) or set(destroyed_ids) != set(key_ids):
        raise RuntimeError("Stalwart 未确认撤销全部指定 API Key")


def read_env(path: Path):
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = None
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as temporary_file:
            descriptor = None
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            # Windows 不支持对目录执行 fsync，文件本身仍已同步并原子替换。
            pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def update_env(path: Path, values):
    if path.exists():
        source = path.read_text(encoding="utf-8")
    else:
        example = path.with_name(".env.example")
        if not example.exists():
            raise RuntimeError(f"找不到 {path} 或 {example}")
        source = example.read_text(encoding="utf-8")
    for key, value in values.items():
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise RuntimeError(f"{key} 包含无法写入环境文件的值")

    lines = source.splitlines()
    remaining = dict(values)
    output = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    for key, value in remaining.items():
        output.append(f"{key}={value}")

    atomic_write(path, ("\n".join(output) + "\n").encode("utf-8"))


def restart_ingress(project_root: Path, env_path: Path):
    command = [
        "docker", "compose", "--env-file", str(env_path),
        "up", "-d", "--build", "--force-recreate", "--no-deps", "email-ingress",
    ]
    try:
        subprocess.run(command, cwd=project_root, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RuntimeError("找不到 Docker 命令，无法激活注册配置") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError("Docker Compose 无法重建 Ingress 注册服务") from error


def ingress_health_target(env_path: Path):
    binding = read_env(env_path).get("INGRESS_HTTP_PORT", "127.0.0.1:4082").strip()
    if not binding:
        raise RuntimeError("INGRESS_HTTP_PORT 配置为空")
    if ":" in binding:
        host, port_text = binding.rsplit(":", 1)
        host = host.strip("[]")
    else:
        host, port_text = "127.0.0.1", binding
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    try:
        port = int(port_text)
    except ValueError as error:
        raise RuntimeError("INGRESS_HTTP_PORT 格式无效") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("INGRESS_HTTP_PORT 端口无效")
    return host, port


def wait_for_ingress(env_path: Path, registration_required: bool, timeout: float = 60):
    host, port = ingress_health_target(env_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection(host, port, timeout=3)
        try:
            connection.request("GET", "/api/email-ingress/health", headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(64 * 1024 + 1)
            if response.status == 200 and len(body) <= 64 * 1024:
                payload = json.loads(body.decode("utf-8"))
                if (
                    isinstance(payload, dict)
                    and payload.get("status") == "healthy"
                    and (not registration_required or payload.get("registration_enabled") is True)
                ):
                    return
        except (OSError, http.client.HTTPException, UnicodeDecodeError, json.JSONDecodeError):
            pass
        finally:
            connection.close()
        time.sleep(1)
    expected = "启用注册功能" if registration_required else "恢复健康状态"
    raise RuntimeError(f"Ingress 重建后未在限时内{expected}")


def activate_ingress(project_root: Path, env_path: Path, registration_required: bool = True):
    restart_ingress(project_root, env_path)
    wait_for_ingress(env_path, registration_required)


def install_registration_key(
    api_url: str,
    authorization: str,
    service_account_id: str,
    env_path: Path,
    configuration,
    activate,
    rollback_activate,
):
    old_key_ids = list_registration_api_key_ids(api_url, authorization, service_account_id)
    new_key_id, token = create_registration_api_key(api_url, authorization, service_account_id)
    previous_env = env_path.read_bytes() if env_path.exists() else None

    try:
        update_env(env_path, {**configuration, "STALWART_PROVISIONING_TOKEN": token})
    except (OSError, RuntimeError) as error:
        try:
            destroy_registration_api_keys(api_url, authorization, service_account_id, [new_key_id])
        except RuntimeError as cleanup_error:
            raise RuntimeError("环境配置写入失败，且新 API Key 撤销失败") from cleanup_error
        raise RuntimeError("环境配置写入失败，新 API Key 已撤销") from error

    try:
        activate()
    except (OSError, RuntimeError) as error:
        rollback_errors = []
        environment_restored = False
        ingress_restored = False
        try:
            if previous_env is None:
                env_path.unlink(missing_ok=True)
            else:
                atomic_write(env_path, previous_env)
            environment_restored = True
        except OSError as rollback_error:
            rollback_errors.append(f"环境文件恢复失败：{rollback_error}")
        if environment_restored:
            try:
                rollback_activate()
                ingress_restored = True
            except (OSError, RuntimeError) as rollback_error:
                rollback_errors.append(f"Ingress 回滚失败：{rollback_error}")
        if environment_restored and ingress_restored:
            try:
                destroy_registration_api_keys(api_url, authorization, service_account_id, [new_key_id])
            except RuntimeError as rollback_error:
                rollback_errors.append(f"新 API Key 撤销失败：{rollback_error}")
            detail = "；".join(rollback_errors)
            message = "新注册令牌激活失败，已恢复旧配置" if not detail else f"新注册令牌激活失败，回滚不完整：{detail}"
        else:
            rollback_errors.append("为保留当前运行实例的恢复路径，新旧 API Key 均未撤销")
            detail = "；".join(rollback_errors)
            message = f"新注册令牌激活失败，需要人工恢复：{detail}"
        raise RuntimeError(message) from error

    try:
        destroy_registration_api_keys(api_url, authorization, service_account_id, old_key_ids)
    except RuntimeError as error:
        raise RuntimeError("新注册令牌已启用，但旧 API Key 清理失败，请立即人工撤销") from error
    return token


def main():
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="配置桌面端邮箱创建服务")
    parser.add_argument("--server-url", default="https://mail.onprs.online")
    parser.add_argument("--admin-user")
    parser.add_argument("--mail-domain")
    parser.add_argument("--rotate-registration-code", action="store_true")
    parser.add_argument("--env-file", type=Path, default=project_root / ".env")
    args = parser.parse_args()
    hold_project_lock(project_root)
    env_path = args.env_file.resolve()

    env_values = read_env(env_path)
    mail_domain = (args.mail_domain or env_values.get("MAIL_DOMAIN") or "onprs.online").strip().lower()
    if not mail_domain or any(character.isspace() for character in mail_domain):
        raise RuntimeError("邮件域名格式无效")

    server = args.server_url.rstrip("/")
    admin_user = args.admin_user or input("Stalwart 管理员账号: ").strip()
    admin_password = getpass.getpass("Stalwart 管理员密码: ")
    if not admin_user or not admin_password:
        raise RuntimeError("管理员账号和密码不能为空")
    authorization = "Basic " + base64.b64encode(f"{admin_user}:{admin_password}".encode("utf-8")).decode("ascii")

    session = get_jmap_session(server, authorization)
    accounts = session.get("accounts") if isinstance(session.get("accounts"), dict) else {}
    primary = session.get("primaryAccounts") if isinstance(session.get("primaryAccounts"), dict) else {}
    admin_account_id = primary.get(STALWART) or primary.get("urn:ietf:params:jmap:mail") or next(iter(accounts), None)
    api_url = session.get("apiUrl")
    if not isinstance(admin_account_id, str) or not admin_account_id or not isinstance(api_url, str):
        raise RuntimeError("管理员 JMAP 会话不完整")
    api_target = urlparse(api_url)
    server_target = urlparse(server)
    api_port = api_target.port or (443 if api_target.scheme == "https" else 80)
    server_port = server_target.port or (443 if server_target.scheme == "https" else 80)
    if (api_target.scheme, api_target.hostname, api_port) != (server_target.scheme, server_target.hostname, server_port):
        raise RuntimeError("JMAP API 地址与配置的服务器不同，已拒绝发送管理员凭据")

    domain_id = discover_domain(api_url, authorization, admin_account_id, mail_domain)
    service_account_id = ensure_service_account(api_url, authorization, admin_account_id, domain_id)

    existing_code = env_values.get("ACCOUNT_REGISTRATION_CODE", "")
    registration_code = (
        secrets.token_hex(32)
        if args.rotate_registration_code or len(existing_code) < 32
        else existing_code
    )
    install_registration_key(
        api_url,
        authorization,
        service_account_id,
        env_path,
        {
            "STALWART_REGISTRATION_DOMAIN_ID": domain_id,
            "ACCOUNT_REGISTRATION_CODE": registration_code,
        },
        lambda: activate_ingress(project_root, env_path),
        lambda: activate_ingress(project_root, env_path, registration_required=False),
    )
    print(f"已将受限配置写入 {env_path} 并重建 Ingress，文件权限已设为仅所有者可读写。")
    print("注册码保存在 ACCOUNT_REGISTRATION_CODE；仅在需要录入客户端时查看该值。")


if __name__ == "__main__":
    configure_utf8_output()
    try:
        main()
    except (RuntimeError, OSError, http.client.HTTPException) as error:
        print(f"配置失败: {error}", file=sys.stderr)
        raise SystemExit(1)
