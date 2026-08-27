#!/usr/bin/env python3
"""
Onprs Email - 通用邮件 Ingress 接收网关服务
功能：
1. 接收来自 Cloudflare Email Worker (Catch-all) 的原始邮件；
2. 自动解析邮件元数据（发件人、收件人、主题、文本、HTML、链接、验证码与附件）；
3. 将邮件持久化至 SQLite 数据库并提供兼容旧接口的查询 API；
4. 尽力将邮件投递至内部 Stalwart SMTP 服务。
"""

import os
import sys
import re
import json
import time
import hmac
import sqlite3
import smtplib
import logging
import http.client
import threading
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote
from typing import List, Dict, Any, Optional, Tuple, Iterable, Iterator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

INGRESS_SECRET = os.environ.get("INGRESS_SECRET_KEY", "")
SMTP_HOST = os.environ.get("STALWART_SMTP_HOST", "stalwart")
SMTP_PORT = int(os.environ.get("STALWART_SMTP_PORT", 25))
DB_PATH = os.environ.get("INGRESS_DB_PATH", "/app/data/ingress_emails.db")
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "onprs.online").lower().strip()
ACCOUNT_REGISTRATION_CODE = os.environ.get("ACCOUNT_REGISTRATION_CODE", "")
STALWART_PROVISIONING_TOKEN = os.environ.get("STALWART_PROVISIONING_TOKEN", "")
STALWART_REGISTRATION_DOMAIN_ID = os.environ.get("STALWART_REGISTRATION_DOMAIN_ID", "").strip()
STALWART_MANAGEMENT_URL = os.environ.get("STALWART_MANAGEMENT_URL", "http://stalwart:8080").rstrip("/")

LOCAL_PART_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?$")
RESERVED_LOCAL_PARTS = {
    "abuse", "admin", "administrator", "hostmaster", "mailer-daemon",
    "noreply", "no-reply", "postmaster", "security", "webmaster",
    "desktop-registration",
}
REGISTRATION_WINDOW_SECONDS = 600
REGISTRATION_ATTEMPT_LIMIT = 8
registration_attempts: Dict[str, List[float]] = {}
registration_attempts_lock = threading.Lock()


class ProvisioningError(Exception):
    def __init__(self, message: str, error_type: str = "serverError"):
        super().__init__(message)
        self.error_type = error_type

if len(INGRESS_SECRET) < 32:
    raise RuntimeError("必须通过 INGRESS_SECRET_KEY 配置至少 32 个字符的 Ingress 通信密钥")

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


def registration_enabled() -> bool:
    return (
        len(ACCOUNT_REGISTRATION_CODE) >= 32
        and bool(STALWART_PROVISIONING_TOKEN)
        and 0 < len(STALWART_REGISTRATION_DOMAIN_ID) <= 512
    )


def stalwart_request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """通过内部网络调用 Stalwart，拒绝重定向以避免配置令牌泄露。"""
    target = urlparse(STALWART_MANAGEMENT_URL)
    if target.scheme not in ("http", "https") or not target.hostname:
        raise ProvisioningError("Stalwart 管理地址配置无效")
    connection_class = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    port = target.port or (443 if target.scheme == "https" else 80)
    connection = connection_class(target.hostname, port, timeout=20)
    base_path = target.path.rstrip("/")
    request_path = f"{base_path}/{path.lstrip('/')}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {STALWART_PROVISIONING_TOKEN}",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, request_path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read(5 * 1024 * 1024 + 1)
    except (OSError, http.client.HTTPException) as error:
        error_type = "commitUnknown" if method == "POST" else "temporarilyUnavailable"
        raise ProvisioningError("无法连接 Stalwart 账号服务", error_type) from error
    finally:
        connection.close()
    if len(response_body) > 5 * 1024 * 1024:
        error_type = "commitUnknown" if method == "POST" else "serverError"
        raise ProvisioningError("Stalwart 返回数据过大", error_type)
    if response.status in (301, 302, 303, 307, 308):
        raise ProvisioningError("Stalwart 账号服务返回了不安全的重定向")
    try:
        parsed = json.loads(response_body.decode("utf-8")) if response_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        error_type = "commitUnknown" if method == "POST" else "serverError"
        raise ProvisioningError("Stalwart 返回数据无法解析", error_type) from error
    if response.status != 200:
        raise ProvisioningError(f"Stalwart 账号服务请求失败（HTTP {response.status}）")
    if not isinstance(parsed, dict):
        raise ProvisioningError("Stalwart 返回数据格式无效")
    return parsed


def stalwart_jmap_call(api_path: str, account_id: str, method_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    uncertain_error_type = "commitUnknown" if method_name == "x:Account/set" else "serverError"
    arguments = {**arguments, "accountId": account_id}
    result = stalwart_request("POST", api_path, {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [[method_name, arguments, "registration"]],
    })
    responses = result.get("methodResponses")
    if not isinstance(responses, list) or not responses:
        raise ProvisioningError("Stalwart 没有返回方法结果", uncertain_error_type)
    response = responses[0]
    if not isinstance(response, list) or len(response) < 2:
        raise ProvisioningError("Stalwart 方法结果格式无效", uncertain_error_type)
    if response[0] == "error":
        details = response[1] if isinstance(response[1], dict) else {}
        raise ProvisioningError(details.get("description", "Stalwart 拒绝了账号操作"), details.get("type", "serverError"))
    if response[0] != method_name or not isinstance(response[1], dict):
        raise ProvisioningError("Stalwart 返回了意外的方法结果", uncertain_error_type)
    return response[1]


def provision_account(local_part: str, password: str, display_name: str = "") -> str:
    """使用受限 Stalwart API 令牌创建固定域名下的普通用户。"""
    session = stalwart_request("GET", "/jmap/session")
    accounts = session.get("accounts") if isinstance(session.get("accounts"), dict) else {}
    primary_accounts = session.get("primaryAccounts") if isinstance(session.get("primaryAccounts"), dict) else {}
    account_id = (
        primary_accounts.get("urn:stalwart:jmap")
        or primary_accounts.get("urn:ietf:params:jmap:mail")
        or next(iter(accounts), None)
    )
    api_url = session.get("apiUrl")
    if not account_id or not isinstance(api_url, str):
        raise ProvisioningError("Stalwart 管理会话不完整")
    api_path = urlparse(api_url).path or "/jmap/"

    create_key = "new-account"
    account = {
        "@type": "User",
        "name": local_part,
        "domainId": STALWART_REGISTRATION_DOMAIN_ID,
        "credentials": {"0": {"@type": "Password", "secret": password}},
        "memberGroupIds": {},
        "encryptionAtRest": {"@type": "Disabled"},
        "permissions": {"@type": "Inherit"},
        "roles": {"@type": "User"},
        "quotas": {},
        "aliases": {},
        "locale": "zh_CN",
    }
    if display_name:
        account["description"] = display_name
    created = stalwart_jmap_call(api_path, account_id, "x:Account/set", {
        "create": {create_key: account},
    })
    not_created = created.get("notCreated")
    if isinstance(not_created, dict) and not_created:
        details = not_created.get(create_key)
        if not isinstance(details, dict):
            details = {}
        raise ProvisioningError(details.get("description", "账号创建失败"), details.get("type", "serverError"))
    created_accounts = created.get("created")
    if not isinstance(created_accounts, dict) or create_key not in created_accounts:
        raise ProvisioningError("Stalwart 没有确认账号创建结果")
    return f"{local_part}@{MAIL_DOMAIN}"


def registration_client_key(handler: BaseHTTPRequestHandler) -> str:
    direct_ip = handler.client_address[0]
    if direct_ip in ("127.0.0.1", "::1"):
        forwarded = handler.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:128]
    return direct_ip[:128]


def registration_rate_limited(client_key: str) -> bool:
    now = time.monotonic()
    cutoff = now - REGISTRATION_WINDOW_SECONDS
    with registration_attempts_lock:
        attempts = [timestamp for timestamp in registration_attempts.get(client_key, []) if timestamp >= cutoff]
        if len(attempts) >= REGISTRATION_ATTEMPT_LIMIT:
            registration_attempts[client_key] = attempts
            return True
        attempts.append(now)
        registration_attempts[client_key] = attempts
        return False


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    """创建启用繁忙等待且会及时关闭的 SQLite 连接。"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    """初始化数据库并兼容升级现有数据卷。"""
    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                mail_from TEXT,
                mail_to TEXT,
                subject TEXT,
                body_text TEXT,
                body_html TEXT,
                links TEXT,
                otp_code TEXT,
                raw_email TEXT,
                created_at REAL,
                is_read INTEGER NOT NULL DEFAULT 0
            )
        """)
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(emails)").fetchall()}
        if "is_read" not in columns:
            cursor.execute("ALTER TABLE emails ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mail_to ON emails(mail_to)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON emails(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_message_id ON emails(message_id)")
        conn.commit()


init_db()


def raw_text_to_bytes(raw_content: str) -> bytes:
    """按 Ingress 投递规则恢复 RFC822 的 CRLF 换行。"""
    normalized = raw_content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    return normalized.encode("utf-8")


def parse_message(raw_bytes: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def parse_raw_email(raw_bytes: bytes) -> Dict[str, Any]:
    """解析 RFC822 格式的原始邮件。"""
    msg = parse_message(raw_bytes)
    subject = msg.get("Subject", "") or ""
    message_id = msg.get("Message-ID", "") or ""

    body_text = ""
    body_html = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                decoded_str = payload.decode(charset, errors="replace")
                if content_type == "text/plain" and not body_text:
                    body_text = decoded_str
                elif content_type == "text/html" and not body_html:
                    body_html = decoded_str
            except Exception as err:
                logging.warning("解析邮件分段失败: %s", err)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded_str = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                body_html = decoded_str
            else:
                body_text = decoded_str

    combined_content = f"{body_text}\n{body_html}"
    links = list(dict.fromkeys(re.findall(r'https?://[^\s<>"\'`]+', combined_content)))
    otp_matches = re.findall(r'\b(?<!\d)(\d{4,8})(?!\d)\b', body_text or body_html)
    otp_code = otp_matches[0] if otp_matches else ""

    return {
        "message_id": message_id,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "links": links,
        "otp_code": otp_code,
    }


def iter_attachments(raw_content: str) -> Iterable[Tuple[str, Any, bytes]]:
    """按稳定的顺序返回附件编号、MIME 分段与解码内容。"""
    msg = parse_message(raw_text_to_bytes(raw_content))
    attachment_index = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition not in ("attachment", "inline") and not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        yield str(attachment_index), part, payload
        attachment_index += 1


def attachment_metadata(raw_content: str) -> List[Dict[str, Any]]:
    result = []
    for part_id, part, payload in iter_attachments(raw_content):
        result.append({
            "part": part_id,
            "name": part.get_filename() or f"attachment-{part_id}",
            "type": part.get_content_type() or "application/octet-stream",
            "size": len(payload),
            "disposition": part.get_content_disposition() or "attachment",
        })
    return result


def find_attachment(raw_content: str, part_id: str) -> Optional[Tuple[Any, bytes]]:
    for current_id, part, payload in iter_attachments(raw_content):
        if current_id == part_id:
            return part, payload
    return None


def save_email(mail_from: str, mail_to: str, raw_content: str, parsed: Dict[str, Any]):
    """保存邮件到 SQLite。"""
    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO emails (
                message_id, mail_from, mail_to, subject, body_text, body_html,
                links, otp_code, raw_email, created_at, is_read
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            parsed.get("message_id", ""),
            mail_from,
            mail_to.lower().strip(),
            parsed.get("subject", ""),
            parsed.get("body_text", ""),
            parsed.get("body_html", ""),
            json.dumps(parsed.get("links", []), ensure_ascii=False),
            parsed.get("otp_code", ""),
            raw_content,
            time.time()
        ))
        conn.commit()


def row_links(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def query_emails(mail_to: Optional[str] = None, since: float = 0, limit: int = 20) -> List[Dict[str, Any]]:
    """旧版接口：按收件人和时间查询完整邮件。"""
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if mail_to:
            cursor.execute("""
                SELECT id, message_id, mail_from, mail_to, subject, body_text,
                       body_html, links, otp_code, created_at, is_read
                FROM emails
                WHERE LOWER(mail_to) = ? AND created_at >= ?
                ORDER BY id DESC LIMIT ?
            """, (mail_to.lower().strip(), since, limit))
        else:
            cursor.execute("""
                SELECT id, message_id, mail_from, mail_to, subject, body_text,
                       body_html, links, otp_code, created_at, is_read
                FROM emails
                WHERE created_at >= ?
                ORDER BY id DESC LIMIT ?
            """, (since, limit))
        return [serialize_full_row(row, include_attachments=False) for row in cursor.fetchall()]


def preview_text(body_text: str, body_html: str, limit: int = 180) -> str:
    source = body_text or re.sub(r"<[^>]+>", " ", body_html or "")
    return re.sub(r"\s+", " ", source).strip()[:limit]


def serialize_summary_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "message_id": row["message_id"] or "",
        "from": row["mail_from"] or "",
        "to": row["mail_to"] or "",
        "subject": row["subject"] or "",
        "preview": preview_text(row["body_text"] or "", row["body_html"] or ""),
        "otp_code": row["otp_code"] or "",
        "has_html": bool(row["body_html"]),
        "created_at": row["created_at"],
        "is_read": bool(row["is_read"]),
    }


def serialize_full_row(row: sqlite3.Row, include_attachments: bool = True) -> Dict[str, Any]:
    result = {
        **serialize_summary_row(row),
        "body_text": row["body_text"] or "",
        "body_html": row["body_html"] or "",
        "links": row_links(row["links"]),
    }
    if include_attachments:
        result["attachments"] = attachment_metadata(row["raw_email"] or "")
    return result


def query_v2_messages(
    mail_to: Optional[str],
    search: Optional[str],
    since: float,
    cursor_id: Optional[int],
    limit: int
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    conditions = ["created_at >= ?"]
    parameters: List[Any] = [since]
    if mail_to:
        conditions.append("LOWER(mail_to) = ?")
        parameters.append(mail_to.lower().strip())
    if search:
        conditions.append("(LOWER(mail_from) LIKE ? OR LOWER(mail_to) LIKE ? OR LOWER(subject) LIKE ? OR LOWER(body_text) LIKE ?)")
        pattern = f"%{search.lower().strip()}%"
        parameters.extend([pattern, pattern, pattern, pattern])
    if cursor_id:
        conditions.append("id < ?")
        parameters.append(cursor_id)

    parameters.append(limit + 1)
    sql = f"""
        SELECT id, message_id, mail_from, mail_to, subject, body_text, body_html,
               links, otp_code, created_at, is_read
        FROM emails
        WHERE {' AND '.join(conditions)}
        ORDER BY id DESC
        LIMIT ?
    """
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, parameters).fetchall()

    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    next_cursor = visible_rows[-1]["id"] if has_more and visible_rows else None
    return [serialize_summary_row(row) for row in visible_rows], next_cursor


def query_recipients(limit: int = 200) -> List[Dict[str, Any]]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT mail_to AS address, COUNT(*) AS count, MAX(created_at) AS latest_at
            FROM emails
            GROUP BY LOWER(mail_to)
            ORDER BY latest_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_email_row(email_id: int) -> Optional[sqlite3.Row]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("""
            SELECT id, message_id, mail_from, mail_to, subject, body_text, body_html,
                   links, otp_code, raw_email, created_at, is_read
            FROM emails WHERE id = ?
        """, (email_id,)).fetchone()


def set_email_read(email_id: int, is_read: bool) -> bool:
    with connect_db() as conn:
        cursor = conn.execute("UPDATE emails SET is_read = ? WHERE id = ?", (1 if is_read else 0, email_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_email(email_id: int) -> bool:
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM emails WHERE id = ?", (email_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_recipient(mail_to: str) -> int:
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM emails WHERE LOWER(mail_to) = ?", (mail_to.lower().strip(),))
        conn.commit()
        return cursor.rowcount


class IngressHandler(BaseHTTPRequestHandler):
    server_version = "OnprsIngress/2"

    def check_auth(self, query_params: Dict[str, List[str]], allow_query_secret: bool = True) -> bool:
        """使用常量时间比较验证共享密钥。"""
        auth_header = self.headers.get("X-Ingress-Secret", "")
        if auth_header and hmac.compare_digest(auth_header, INGRESS_SECRET):
            return True
        if allow_query_secret:
            query_secret = query_params.get("secret", [""])[0]
            if query_secret and hmac.compare_digest(query_secret, INGRESS_SECRET):
                return True
        return False

    def send_common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_json(self, status_code: int, data: Any):
        response_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_common_headers()
        self.end_headers()
        self.wfile.write(response_bytes)

    def send_binary(self, status_code: int, data: bytes, content_type: str, filename: str):
        safe_name = re.sub(r"[\r\n\x00-\x1f]", "_", filename or "download")
        self.send_response(status_code)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(safe_name)}")
        self.send_common_headers()
        self.end_headers()
        self.wfile.write(data)

    def require_v2_auth(self, query: Dict[str, List[str]]) -> bool:
        if self.check_auth(query, allow_query_secret=False):
            return True
        self.send_json(401, {"error": "Unauthorized"})
        return False

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/") or "/"
        query = parse_qs(parsed_url.query)

        if path in ("/health", "/api/email-ingress/health"):
            self.send_json(200, {
                "status": "healthy",
                "service": "onprs-email-ingress",
                "api_version": 2,
                "registration_enabled": registration_enabled(),
            })
            return

        if path == "/api/email-ingress/messages":
            if not self.check_auth(query):
                self.send_json(401, {"error": "Unauthorized"})
                return
            try:
                mail_to = query.get("to", [None])[0]
                since = float(query.get("since", ["0"])[0])
                limit = min(max(int(query.get("limit", ["20"])[0]), 1), 100)
            except ValueError:
                self.send_json(400, {"error": "Invalid since or limit parameter"})
                return
            emails = query_emails(mail_to=mail_to, since=since, limit=limit)
            self.send_json(200, {"status": "success", "count": len(emails), "data": emails})
            return

        if path == "/api/email-ingress/v2/messages":
            if not self.require_v2_auth(query):
                return
            try:
                mail_to = query.get("to", [None])[0]
                search = query.get("q", [None])[0]
                since = float(query.get("since", ["0"])[0])
                cursor_value = query.get("cursor", [None])[0]
                cursor_id = int(cursor_value) if cursor_value else None
                limit = min(max(int(query.get("limit", ["50"])[0]), 1), 100)
            except ValueError:
                self.send_json(400, {"error": "Invalid query parameter"})
                return
            items, next_cursor = query_v2_messages(mail_to, search, since, cursor_id, limit)
            self.send_json(200, {
                "status": "success",
                "items": items,
                "next_cursor": next_cursor,
                "recipients": query_recipients(),
            })
            return

        detail_match = re.fullmatch(r"/api/email-ingress/v2/messages/(\d+)", path)
        if detail_match:
            if not self.require_v2_auth(query):
                return
            email_id = int(detail_match.group(1))
            row = get_email_row(email_id)
            if not row:
                self.send_json(404, {"error": "Message not found"})
                return
            set_email_read(email_id, True)
            data = serialize_full_row(row)
            data["is_read"] = True
            self.send_json(200, {"status": "success", "data": data})
            return

        raw_match = re.fullmatch(r"/api/email-ingress/v2/messages/(\d+)/raw", path)
        if raw_match:
            if not self.require_v2_auth(query):
                return
            row = get_email_row(int(raw_match.group(1)))
            if not row:
                self.send_json(404, {"error": "Message not found"})
                return
            raw_bytes = raw_text_to_bytes(row["raw_email"] or "")
            self.send_binary(200, raw_bytes, "message/rfc822", f"message-{row['id']}.eml")
            return

        attachment_match = re.fullmatch(r"/api/email-ingress/v2/messages/(\d+)/attachments/([^/]+)", path)
        if attachment_match:
            if not self.require_v2_auth(query):
                return
            row = get_email_row(int(attachment_match.group(1)))
            if not row:
                self.send_json(404, {"error": "Message not found"})
                return
            attachment = find_attachment(row["raw_email"] or "", unquote(attachment_match.group(2)))
            if not attachment:
                self.send_json(404, {"error": "Attachment not found"})
                return
            part, payload = attachment
            self.send_binary(
                200,
                payload,
                part.get_content_type() or "application/octet-stream",
                part.get_filename() or "attachment"
            )
            return

        self.send_json(404, {"error": "Not Found"})

    def do_PATCH(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query = parse_qs(parsed_url.query)
        detail_match = re.fullmatch(r"/api/email-ingress/v2/messages/(\d+)", path)
        if not detail_match:
            self.send_json(404, {"error": "Not Found"})
            return
        if not self.require_v2_auth(query):
            return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > 4096:
                raise ValueError("invalid length")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload.get("is_read"), bool):
                raise ValueError("invalid is_read")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid request body"})
            return
        if not set_email_read(int(detail_match.group(1)), payload["is_read"]):
            self.send_json(404, {"error": "Message not found"})
            return
        self.send_json(200, {"status": "success"})

    def do_DELETE(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query = parse_qs(parsed_url.query)

        if path == "/api/email-ingress/messages":
            if not self.check_auth(query):
                self.send_json(401, {"error": "Unauthorized"})
                return
            mail_to = query.get("to", [None])[0]
            with connect_db() as conn:
                if mail_to:
                    conn.execute("DELETE FROM emails WHERE LOWER(mail_to) = ?", (mail_to.lower().strip(),))
                else:
                    conn.execute("DELETE FROM emails")
                conn.commit()
            self.send_json(200, {"status": "success", "message": "Messages deleted"})
            return

        if path == "/api/email-ingress/v2/messages":
            if not self.require_v2_auth(query):
                return
            mail_to = query.get("to", [None])[0]
            if not mail_to:
                self.send_json(400, {"error": "Recipient is required"})
                return
            deleted = delete_recipient(mail_to)
            self.send_json(200, {"status": "success", "deleted": deleted})
            return

        detail_match = re.fullmatch(r"/api/email-ingress/v2/messages/(\d+)", path)
        if detail_match:
            if not self.require_v2_auth(query):
                return
            if not delete_email(int(detail_match.group(1))):
                self.send_json(404, {"error": "Message not found"})
                return
            self.send_json(200, {"status": "success"})
            return

        self.send_json(404, {"error": "Not Found"})

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query = parse_qs(parsed_url.query)

        if path == "/api/email-ingress/v2/accounts":
            if not registration_enabled():
                self.send_json(503, {
                    "error": "Registration is not configured",
                    "code": "registration_not_configured",
                })
                return
            if registration_rate_limited(registration_client_key(self)):
                self.send_json(429, {"error": "Too many registration attempts"})
                return
            registration_code = self.headers.get("X-Registration-Code", "")
            if not registration_code or not hmac.compare_digest(registration_code, ACCOUNT_REGISTRATION_CODE):
                self.send_json(401, {
                    "error": "Invalid registration code",
                    "code": "registration_code_invalid",
                })
                return
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length <= 0 or content_length > 16 * 1024:
                    raise ValueError("invalid length")
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("invalid payload")
                local_part = payload.get("local_part")
                password = payload.get("password")
                display_name = payload.get("display_name", "")
                if not isinstance(local_part, str) or not isinstance(password, str) or not isinstance(display_name, str):
                    raise ValueError("invalid fields")
                local_part = local_part.strip().lower()
                display_name = display_name.strip()
                if not LOCAL_PART_PATTERN.fullmatch(local_part) or local_part in RESERVED_LOCAL_PARTS:
                    self.send_json(422, {"error": "邮箱名不可用"})
                    return
                if len(password) < 12 or len(password) > 128:
                    self.send_json(422, {"error": "密码长度必须为 12 至 128 个字符"})
                    return
                if len(display_name) > 120:
                    self.send_json(422, {"error": "显示名称过长"})
                    return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "Invalid registration request"})
                return

            try:
                email = provision_account(local_part, password, display_name)
            except ProvisioningError as error:
                error_type = error.error_type.lower()
                error_message = str(error)
                if error_type in ("alreadyexists", "accountalreadyexists", "primarykeyviolation") or "already" in error_message.lower():
                    self.send_json(409, {"error": "邮箱地址已被使用"})
                elif error_type == "commitunknown":
                    logging.error("Stalwart 账号创建结果未知: detail=%s", error_message)
                    self.send_json(503, {
                        "error": "Account creation result is unknown",
                        "code": "registration_commit_unknown",
                    })
                elif error_type in ("invalidproperties", "invalidarguments"):
                    self.send_json(422, {"error": error_message})
                else:
                    logging.error("Stalwart 账号创建失败: type=%s, detail=%s", error.error_type, error_message)
                    self.send_json(503, {
                        "error": "Account provisioning unavailable",
                        "code": "provisioning_unavailable",
                    })
                return

            logging.info("已创建普通邮箱账号: %s", email)
            self.send_json(201, {"status": "success", "email": email})
            return

        if path != "/api/email-ingress":
            self.send_json(404, {"error": "Not Found"})
            return

        if not self.check_auth(query):
            logging.warning("拒绝未授权的 Ingress 请求: Secret 不匹配")
            self.send_json(401, {"error": "Unauthorized"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > 30 * 1024 * 1024:
                self.send_json(400, {"error": "Invalid Content-Length"})
                return

            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            mail_from = payload.get("from")
            mail_to = payload.get("to")
            raw_email = payload.get("raw")

            if not mail_from or not mail_to or not raw_email:
                self.send_json(400, {"error": "Missing from, to or raw fields"})
                return

            recipients = [mail_to] if isinstance(mail_to, str) else list(mail_to)
            if not recipients or not all(isinstance(recipient, str) and recipient.strip() for recipient in recipients):
                self.send_json(400, {"error": "Invalid recipient list"})
                return

            logging.info("收到邮件投递请求: From=<%s>, To=%s", mail_from, recipients)
            if isinstance(raw_email, str):
                raw_bytes = raw_text_to_bytes(raw_email)
                raw_text = raw_email
            else:
                self.send_json(400, {"error": "Invalid raw field"})
                return

            parsed_data: Dict[str, Any] = {
                "message_id": "",
                "subject": "",
                "body_text": "",
                "body_html": "",
                "links": [],
                "otp_code": "",
            }
            try:
                parsed_data = parse_raw_email(raw_bytes)
                for recipient in recipients:
                    save_email(mail_from, recipient, raw_text, parsed_data)
                logging.info("邮件已解析并存入数据库: Subject='%s', To=%s", parsed_data.get("subject"), recipients)
            except Exception as parse_err:
                logging.error("邮件解析存库异常: %s", parse_err, exc_info=True)

            try:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp_client:
                    smtp_client.ehlo("mail.onprs.online")
                    smtp_client.sendmail(mail_from, recipients, raw_bytes)
                logging.info("邮件成功同步投递至 Stalwart SMTP: To=%s", recipients)
            except Exception as smtp_err:
                logging.warning("Stalwart SMTP 投递提示: %s (邮件已在 Ingress 数据库中保留)", str(smtp_err))

            self.send_json(200, {
                "status": "success",
                "message": "Email received and processed",
                "subject": parsed_data.get("subject", ""),
                "to": recipients
            })

        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "Invalid request payload"})
        except Exception as err:
            logging.error("邮件投递处理失败: %s", str(err), exc_info=True)
            self.send_json(500, {"error": "Internal Server Error"})

    def log_message(self, format, *args):
        pass


def run_server():
    port = 8080
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, IngressHandler)
    logging.info("通用邮件 Ingress 接收网关正在监听 0.0.0.0:%d ...", port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logging.info("Ingress 服务已停止。")


if __name__ == "__main__":
    run_server()
