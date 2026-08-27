#!/usr/bin/env python3
"""Ingress v2 的数据库与 HTTP 契约测试。"""

import json
import os
import sys
import tempfile
import threading
import unittest
from email.message import EmailMessage
from email import policy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TEST_DATA = tempfile.TemporaryDirectory()
os.environ["INGRESS_DB_PATH"] = str(Path(TEST_DATA.name) / "ingress-test.db")
os.environ["INGRESS_SECRET_KEY"] = "test-ingress-secret-1234567890abcdef"
os.environ["ACCOUNT_REGISTRATION_CODE"] = "test-registration-code-1234567890abcdef"
os.environ["STALWART_PROVISIONING_TOKEN"] = "API_test_provisioning_token"
os.environ["STALWART_REGISTRATION_DOMAIN_ID"] = "domain-test-id"
sys.path.insert(0, str(Path(__file__).parent))

import app as ingress  # noqa: E402


def sample_raw(subject: str = "登录验证码") -> str:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "login@onprs.online"
    message["Subject"] = subject
    message["Message-ID"] = f"<{subject}@example.com>"
    message.set_content("你的验证码是 482913，访问 https://onprs.online/verify")
    message.add_alternative("<p>你的验证码是 <strong>482913</strong></p>", subtype="html")
    message.add_attachment(b"attachment-content", maintype="application", subtype="octet-stream", filename="report.txt")
    return message.as_string(policy=policy.SMTP)


class IngressApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ingress.ThreadingHTTPServer(("127.0.0.1", 0), ingress.IngressHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        TEST_DATA.cleanup()

    def setUp(self):
        with ingress.connect_db() as conn:
            conn.execute("DELETE FROM emails")
            conn.commit()
        with ingress.registration_attempts_lock:
            ingress.registration_attempts.clear()

    def save_sample(self, recipient: str = "login@onprs.online", subject: str = "登录验证码") -> int:
        raw = sample_raw(subject)
        parsed = ingress.parse_raw_email(ingress.raw_text_to_bytes(raw))
        ingress.save_email("sender@example.com", recipient, raw, parsed)
        with ingress.connect_db() as conn:
            return conn.execute("SELECT id FROM emails ORDER BY id DESC LIMIT 1").fetchone()[0]

    def request(self, path: str, method: str = "GET", with_header: bool = True, body=None, extra_headers=None):
        headers = dict(extra_headers or {})
        if with_header:
            headers["X-Ingress-Secret"] = os.environ["INGRESS_SECRET_KEY"]
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers, data=data)
        with urlopen(request, timeout=5) as response:
            content_type = response.headers.get("Content-Type", "")
            payload = response.read()
            if "application/json" in content_type:
                return response.status, json.loads(payload.decode("utf-8")), response.headers
            return response.status, payload, response.headers

    def test_v2游标分页和地址聚合(self):
        self.save_sample("first@onprs.online", "第一封")
        second_id = self.save_sample("second@onprs.online", "第二封")
        self.save_sample("first@onprs.online", "第三封")

        items, cursor = ingress.query_v2_messages(None, None, 0, None, 2)
        self.assertEqual(len(items), 2)
        self.assertIsNotNone(cursor)
        next_items, next_cursor = ingress.query_v2_messages(None, None, 0, cursor, 2)
        self.assertEqual(len(next_items), 1)
        self.assertIsNone(next_cursor)
        self.assertTrue(any(item["id"] == second_id for item in items + next_items))

        recipients = ingress.query_recipients()
        counts = {item["address"]: item["count"] for item in recipients}
        self.assertEqual(counts["first@onprs.online"], 2)
        self.assertEqual(counts["second@onprs.online"], 1)

    def test详情原始邮件和附件(self):
        email_id = self.save_sample()
        status, detail, _ = self.request(f"/api/email-ingress/v2/messages/{email_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["data"]["otp_code"], "482913")
        self.assertTrue(detail["data"]["is_read"])
        self.assertEqual(detail["data"]["attachments"][0]["name"], "report.txt")

        status, attachment, headers = self.request(f"/api/email-ingress/v2/messages/{email_id}/attachments/0")
        self.assertEqual(status, 200)
        self.assertEqual(attachment, b"attachment-content")
        self.assertIn("report.txt", headers["Content-Disposition"])

        status, raw, headers = self.request(f"/api/email-ingress/v2/messages/{email_id}/raw")
        self.assertEqual(status, 200)
        self.assertIn(b"Subject:", raw)
        self.assertEqual(headers.get_content_type(), "message/rfc822")

    def test_v2只接受请求头密钥(self):
        self.save_sample()
        with self.assertRaises(HTTPError) as context:
            self.request("/api/email-ingress/v2/messages?secret=test-ingress-secret-1234567890abcdef", with_header=False)
        self.assertEqual(context.exception.code, 401)

        status, payload, _ = self.request("/api/email-ingress/v2/messages?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 1)

    def test桌面端注册码创建普通邮箱(self):
        body = {
            "local_part": "new.user",
            "password": "StrongPassword-1234",
            "display_name": "新用户",
        }
        with patch.object(ingress, "provision_account", return_value="new.user@onprs.online") as provision:
            status, payload, _ = self.request(
                "/api/email-ingress/v2/accounts",
                method="POST",
                with_header=False,
                body=body,
                extra_headers={"X-Registration-Code": os.environ["ACCOUNT_REGISTRATION_CODE"]},
            )
        self.assertEqual(status, 201)
        self.assertEqual(payload["email"], "new.user@onprs.online")
        provision.assert_called_once_with("new.user", "StrongPassword-1234", "新用户")

    def test重复邮箱映射为冲突响应(self):
        body = {
            "local_part": "existing.user",
            "password": "StrongPassword-1234",
            "display_name": "既有用户",
        }
        conflict = ingress.ProvisioningError("Primary key already exists", "primaryKeyViolation")
        with patch.object(ingress, "provision_account", side_effect=conflict):
            with self.assertRaises(HTTPError) as context:
                self.request(
                    "/api/email-ingress/v2/accounts",
                    method="POST",
                    with_header=False,
                    body=body,
                    extra_headers={"X-Registration-Code": os.environ["ACCOUNT_REGISTRATION_CODE"]},
                )
        self.assertEqual(context.exception.code, 409)

    def test创建结果未知返回可恢复机器码(self):
        body = {
            "local_part": "new.user",
            "password": "StrongPassword-1234",
            "display_name": "新用户",
        }
        unknown = ingress.ProvisioningError("socket closed", "commitUnknown")
        with patch.object(ingress, "provision_account", side_effect=unknown):
            with self.assertRaises(HTTPError) as context:
                self.request(
                    "/api/email-ingress/v2/accounts",
                    method="POST",
                    with_header=False,
                    body=body,
                    extra_headers={"X-Registration-Code": os.environ["ACCOUNT_REGISTRATION_CODE"]},
                )
        self.assertEqual(context.exception.code, 503)
        payload = json.loads(context.exception.read().decode("utf-8"))
        self.assertEqual(payload["code"], "registration_commit_unknown")

    def test账号创建响应缺失被标记为结果未知(self):
        with patch.object(ingress, "stalwart_request", return_value={"methodResponses": []}):
            with self.assertRaises(ingress.ProvisioningError) as context:
                ingress.stalwart_jmap_call("/jmap/", "service-account", "x:Account/set", {"create": {}})
        self.assertEqual(context.exception.error_type, "commitUnknown")

    def test未配置响应包含稳定机器码(self):
        body = {
            "local_part": "new.user",
            "password": "StrongPassword-1234",
            "display_name": "新用户",
        }
        with patch.object(ingress, "STALWART_REGISTRATION_DOMAIN_ID", ""):
            with self.assertRaises(HTTPError) as context:
                self.request(
                    "/api/email-ingress/v2/accounts",
                    method="POST",
                    with_header=False,
                    body=body,
                    extra_headers={"X-Registration-Code": os.environ["ACCOUNT_REGISTRATION_CODE"]},
                )
        self.assertEqual(context.exception.code, 503)
        payload = json.loads(context.exception.read().decode("utf-8"))
        self.assertEqual(payload["code"], "registration_not_configured")

    def test配置令牌仅创建固定域普通用户(self):
        session = {
            "accounts": {"service-account": {"name": "desktop-registration@onprs.online"}},
            "primaryAccounts": {"urn:stalwart:jmap": "service-account"},
            "apiUrl": "http://stalwart:8080/jmap/",
        }
        created = {"created": {"new-account": {"id": "created-account"}}}
        with (
            patch.object(ingress, "stalwart_request", return_value=session) as request,
            patch.object(ingress, "stalwart_jmap_call", return_value=created) as call,
        ):
            email = ingress.provision_account("new.user", "StrongPassword-1234", "新用户")

        self.assertEqual(email, "new.user@onprs.online")
        request.assert_called_once_with("GET", "/jmap/session")
        call.assert_called_once()
        api_path, account_id, method_name, arguments = call.call_args.args
        self.assertEqual((api_path, account_id, method_name), ("/jmap/", "service-account", "x:Account/set"))
        account = arguments["create"]["new-account"]
        self.assertEqual(account["domainId"], "domain-test-id")
        self.assertEqual(account["roles"], {"@type": "User"})
        self.assertEqual(account["permissions"], {"@type": "Inherit"})
        self.assertNotIn("memberTenantId", account)

    def test缺少固定域标识时停用注册(self):
        with patch.object(ingress, "STALWART_REGISTRATION_DOMAIN_ID", ""):
            self.assertFalse(ingress.registration_enabled())

    def test邮箱创建拒绝无效注册码和保留地址(self):
        body = {"local_part": "new-user", "password": "StrongPassword-1234", "display_name": ""}
        with self.assertRaises(HTTPError) as invalid_code:
            self.request(
                "/api/email-ingress/v2/accounts",
                method="POST",
                with_header=False,
                body=body,
                extra_headers={"X-Registration-Code": "wrong-registration-code-1234567890"},
            )
        self.assertEqual(invalid_code.exception.code, 401)
        invalid_payload = json.loads(invalid_code.exception.read().decode("utf-8"))
        self.assertEqual(invalid_payload["code"], "registration_code_invalid")

        body["local_part"] = "admin"
        with self.assertRaises(HTTPError) as reserved_name:
            self.request(
                "/api/email-ingress/v2/accounts",
                method="POST",
                with_header=False,
                body=body,
                extra_headers={"X-Registration-Code": os.environ["ACCOUNT_REGISTRATION_CODE"]},
            )
        self.assertEqual(reserved_name.exception.code, 422)

    def test单封状态和删除(self):
        email_id = self.save_sample()
        status, _, _ = self.request(f"/api/email-ingress/v2/messages/{email_id}", method="PATCH", body={"is_read": True})
        self.assertEqual(status, 200)
        self.assertTrue(ingress.get_email_row(email_id)["is_read"])

        status, _, _ = self.request(f"/api/email-ingress/v2/messages/{email_id}", method="DELETE")
        self.assertEqual(status, 200)
        self.assertIsNone(ingress.get_email_row(email_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
