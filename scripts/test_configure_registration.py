#!/usr/bin/env python3
"""configure-registration.py 的 Stalwart 请求与轮换事务测试。"""

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).with_name("configure-registration.py")
SPEC = importlib.util.spec_from_file_location("configure_registration", SCRIPT_PATH)
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)


class RegistrationConfigurationTest(unittest.TestCase):
    def test非Utf8终端改用Utf8输出(self):
        class OutputStream:
            def __init__(self):
                self.options = None

            def reconfigure(self, **options):
                self.options = options

        stdout = OutputStream()
        stderr = OutputStream()
        registration.configure_utf8_output((stdout, stderr))

        self.assertEqual(stdout.options, {"encoding": "utf-8", "errors": "backslashreplace"})
        self.assertEqual(stderr.options, {"encoding": "utf-8", "errors": "backslashreplace"})

    def test会话发现直接使用无重定向端点(self):
        expected = {"apiUrl": "https://mail.onprs.online/jmap/"}
        with patch.object(registration, "request_json", return_value=expected) as request:
            result = registration.get_jmap_session("https://mail.onprs.online", "Basic admin")

        self.assertEqual(result, expected)
        request.assert_called_once_with(
            "https://mail.onprs.online/jmap/session",
            "GET",
            "Basic admin",
        )

    def test专用主体只额外获得账号创建权限(self):
        payload = registration.service_account_payload("domain-id")
        self.assertEqual(payload["@type"], "User")
        self.assertEqual(payload["credentials"], {})
        self.assertEqual(payload["roles"], {"@type": "User"})
        self.assertEqual(payload["permissions"], {
            "@type": "Merge",
            "enabledPermissions": {"sysAccountCreate": True},
            "disabledPermissions": {},
        })

    def test创建专用主体后返回其标识(self):
        responses = [
            {"ids": []},
            {"created": {"registration-service": {"id": "service-id"}}},
        ]
        with patch.object(registration, "jmap_call", side_effect=responses) as call:
            account_id = registration.ensure_service_account(
                "https://mail.onprs.online/jmap/",
                "Basic admin",
                "admin-id",
                "domain-id",
            )

        self.assertEqual(account_id, "service-id")
        method_name = call.call_args_list[1].args[3]
        arguments = call.call_args_list[1].args[4]
        self.assertEqual(method_name, "x:Account/set")
        account = arguments["create"]["registration-service"]
        self.assertEqual(account["name"], "desktop-registration")
        self.assertEqual(account["domainId"], "domain-id")
        self.assertNotIn("memberTenantId", account)

    def test新密钥继承专用主体权限(self):
        response = {"created": {"registration-key": {"id": "new-key", "secret": "API_new_secret"}}}
        with patch.object(registration, "jmap_call", return_value=response) as call:
            key_id, token = registration.create_registration_api_key(
                "https://mail.onprs.online/jmap/",
                "Basic admin",
                "service-id",
            )

        self.assertEqual((key_id, token), ("new-key", "API_new_secret"))
        api_key = call.call_args.args[4]["create"]["registration-key"]
        self.assertEqual(api_key["permissions"], {"@type": "Inherit"})
        self.assertEqual(api_key["allowedIps"], {})
        self.assertNotIn("enabledPermissions", api_key["permissions"])

    def test只清理脚本标记的旧密钥(self):
        response = {"list": [
            {"id": "managed", "description": registration.API_KEY_DESCRIPTION},
            {"id": "foreign", "description": "其他用途"},
        ]}
        with patch.object(registration, "jmap_call", return_value=response):
            ids = registration.list_registration_api_key_ids(
                "https://mail.onprs.online/jmap/",
                "Basic admin",
                "service-id",
            )
        self.assertEqual(ids, ["managed"])

    def test拒绝复用含密码的同名主体(self):
        responses = [
            {"ids": ["service-id"]},
            {"list": [{
                "id": "service-id",
                "name": "desktop-registration",
                "domainId": "domain-id",
                "description": registration.SERVICE_ACCOUNT_DESCRIPTION,
                "credentials": {"0": {"@type": "Password", "secret": "*****"}},
            }]},
        ]
        with patch.object(registration, "jmap_call", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "非 API Key"):
                registration.ensure_service_account(
                    "https://mail.onprs.online/jmap/",
                    "Basic admin",
                    "admin-id",
                    "domain-id",
                )

    def test环境文件更新时保留既有值(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("KEEP=value\nTOKEN=old\n", encoding="utf-8")
            registration.update_env(env_path, {"TOKEN": "new", "DOMAIN": "domain-id"})
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "KEEP=value\nTOKEN=new\nDOMAIN=domain-id\n",
            )
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test新密钥激活健康后才清理旧密钥(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("STALWART_PROVISIONING_TOKEN=old-token\n", encoding="utf-8")
            events = []

            with (
                patch.object(registration, "list_registration_api_key_ids", side_effect=lambda *_: events.append("list") or ["old-key"]),
                patch.object(registration, "create_registration_api_key", side_effect=lambda *_: events.append("create") or ("new-key", "new-token")),
                patch.object(registration, "destroy_registration_api_keys", side_effect=lambda *args: events.append(("destroy", args[-1]))),
            ):
                registration.install_registration_key(
                    "api", "auth", "service", env_path,
                    {"STALWART_REGISTRATION_DOMAIN_ID": "domain-id"},
                    lambda: events.append("healthy"),
                    lambda: events.append("rollback"),
                )

            self.assertEqual(events, ["list", "create", "healthy", ("destroy", ["old-key"])])
            values = registration.read_env(env_path)
            self.assertEqual(values["STALWART_PROVISIONING_TOKEN"], "new-token")
            self.assertEqual(values["STALWART_REGISTRATION_DOMAIN_ID"], "domain-id")

    def test环境写入失败时撤销新密钥并保留旧密钥(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("STALWART_PROVISIONING_TOKEN=old-token\n", encoding="utf-8")
            destroyed = []

            with (
                patch.object(registration, "list_registration_api_key_ids", return_value=["old-key"]),
                patch.object(registration, "create_registration_api_key", return_value=("new-key", "new-token")),
                patch.object(registration, "update_env", side_effect=OSError("disk full")),
                patch.object(registration, "destroy_registration_api_keys", side_effect=lambda *args: destroyed.append(args[-1])),
            ):
                with self.assertRaisesRegex(RuntimeError, "新 API Key 已撤销"):
                    registration.install_registration_key(
                        "api", "auth", "service", env_path, {},
                        lambda: self.fail("不应激活"),
                        lambda: self.fail("不应回滚服务"),
                    )

            self.assertEqual(destroyed, [["new-key"]])
            self.assertEqual(registration.read_env(env_path)["STALWART_PROVISIONING_TOKEN"], "old-token")

    def test激活失败时恢复旧环境并只撤销新密钥(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            old_content = b"STALWART_PROVISIONING_TOKEN=old-token\nKEEP=value\n"
            env_path.write_bytes(old_content)
            destroyed = []
            rollbacks = []

            with (
                patch.object(registration, "list_registration_api_key_ids", return_value=["old-key"]),
                patch.object(registration, "create_registration_api_key", return_value=("new-key", "new-token")),
                patch.object(registration, "destroy_registration_api_keys", side_effect=lambda *args: destroyed.append(args[-1])),
            ):
                with self.assertRaisesRegex(RuntimeError, "已恢复旧配置"):
                    registration.install_registration_key(
                        "api", "auth", "service", env_path, {},
                        lambda: (_ for _ in ()).throw(RuntimeError("health failed")),
                        lambda: rollbacks.append("restarted"),
                    )

            self.assertEqual(env_path.read_bytes(), old_content)
            self.assertEqual(rollbacks, ["restarted"])
            self.assertEqual(destroyed, [["new-key"]])

    def test环境恢复失败时保留新旧密钥(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("STALWART_PROVISIONING_TOKEN=old-token\n", encoding="utf-8")
            destroyed = []
            real_atomic_write = registration.atomic_write
            atomic_calls = 0

            def fail_rollback_write(path, data):
                nonlocal atomic_calls
                atomic_calls += 1
                if atomic_calls == 1:
                    return real_atomic_write(path, data)
                raise OSError("restore failed")

            with (
                patch.object(registration, "list_registration_api_key_ids", return_value=["old-key"]),
                patch.object(registration, "create_registration_api_key", return_value=("new-key", "new-token")),
                patch.object(registration, "atomic_write", side_effect=fail_rollback_write),
                patch.object(registration, "destroy_registration_api_keys", side_effect=lambda *args: destroyed.append(args[-1])),
            ):
                with self.assertRaisesRegex(RuntimeError, "需要人工恢复"):
                    registration.install_registration_key(
                        "api", "auth", "service", env_path, {},
                        lambda: (_ for _ in ()).throw(RuntimeError("health failed")),
                        lambda: self.fail("环境未恢复时不应重启旧配置"),
                    )

            self.assertEqual(destroyed, [])
            self.assertEqual(registration.read_env(env_path)["STALWART_PROVISIONING_TOKEN"], "new-token")

    def test旧Ingress回滚失败时保留新旧密钥(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("STALWART_PROVISIONING_TOKEN=old-token\n", encoding="utf-8")
            destroyed = []

            with (
                patch.object(registration, "list_registration_api_key_ids", return_value=["old-key"]),
                patch.object(registration, "create_registration_api_key", return_value=("new-key", "new-token")),
                patch.object(registration, "destroy_registration_api_keys", side_effect=lambda *args: destroyed.append(args[-1])),
            ):
                with self.assertRaisesRegex(RuntimeError, "需要人工恢复"):
                    registration.install_registration_key(
                        "api", "auth", "service", env_path, {},
                        lambda: (_ for _ in ()).throw(RuntimeError("health failed")),
                        lambda: (_ for _ in ()).throw(RuntimeError("rollback failed")),
                    )

            self.assertEqual(destroyed, [])
            self.assertEqual(registration.read_env(env_path)["STALWART_PROVISIONING_TOKEN"], "old-token")

    def test项目锁阻止第二个配置进程并发进入(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            child_code = f"""
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('registration_child', {str(SCRIPT_PATH)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.hold_project_lock(Path({str(project_root)!r}))
print('acquired', flush=True)
"""
            registration.hold_project_lock(project_root)
            process = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                time.sleep(0.3)
                self.assertIsNone(process.poll())
                registration.release_project_lock()
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(stdout.strip(), "acquired")
            finally:
                registration.release_project_lock()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test新密钥创建失败时不修改环境或旧密钥(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            old_content = b"STALWART_PROVISIONING_TOKEN=old-token\n"
            env_path.write_bytes(old_content)

            with (
                patch.object(registration, "list_registration_api_key_ids", return_value=["old-key"]),
                patch.object(registration, "create_registration_api_key", side_effect=RuntimeError("quota")),
                patch.object(registration, "destroy_registration_api_keys") as destroy,
            ):
                with self.assertRaisesRegex(RuntimeError, "quota"):
                    registration.install_registration_key(
                        "api", "auth", "service", env_path, {},
                        lambda: self.fail("不应激活"),
                        lambda: self.fail("不应回滚"),
                    )

            self.assertEqual(env_path.read_bytes(), old_content)
            destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
