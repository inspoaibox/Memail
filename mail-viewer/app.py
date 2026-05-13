import ipaddress
import base64
import hmac
import json
import hashlib
import html as html_lib
import os
import threading
import re
import secrets
import socket
import struct
import time
import uuid
import requests
import bleach
from functools import wraps
from datetime import datetime, timezone, timedelta
from bleach.css_sanitizer import CSSSanitizer
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response, stream_with_context
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
APP_SECRET = os.getenv("APP_SECRET", "")
app.secret_key = os.getenv("SECRET_KEY") or APP_SECRET or "mail-viewer-secret-key-change-me"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))),
)

if app.secret_key == "mail-viewer-secret-key-change-me":
    import warnings
    warnings.warn("⚠️ SECRET_KEY is using default value! Set it via environment variable in production!")

# 访问密码（从环境变量读取）
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD_HASH = (os.getenv("ADMIN_PASSWORD_HASH") or os.getenv("ACCESS_PASSWORD_HASH") or "").strip()
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))

# DuckMail API 配置
DUCKMAIL_BASE_URL = os.getenv("DUCKMAIL_BASE_URL", "http://127.0.0.1:8080")
DUCKMAIL_API_KEY = os.getenv("DUCKMAIL_API_KEY") or os.getenv("API_KEY") or APP_SECRET
UNIFIED_PASSWORD = os.getenv("UNIFIED_PASSWORD", "")
IMAP_MAIL_BASE_URL = os.getenv("IMAP_MAIL_BASE_URL", "http://imap-mail:3939")

# Resend 发信配置
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
VIEWER_SETTINGS_FILE = os.getenv("VIEWER_SETTINGS_FILE", os.path.join(os.getcwd(), "viewer-settings.json"))
CONFIG_ENCRYPTION_KEY = (
    os.getenv("CONFIG_ENCRYPTION_KEY")
    or os.getenv("IMAP_ACCOUNTS_SECRET")
    or APP_SECRET
    or app.secret_key
    or ""
)
MAX_IMAGE_PROXY_BYTES = int(os.getenv("MAX_IMAGE_PROXY_BYTES", str(5 * 1024 * 1024)))
IMAGE_PROXY_CONNECT_TIMEOUT_SECONDS = float(os.getenv("IMAGE_PROXY_CONNECT_TIMEOUT_SECONDS", "3"))
IMAGE_PROXY_READ_TIMEOUT_SECONDS = float(os.getenv("IMAGE_PROXY_READ_TIMEOUT_SECONDS", "8"))
AI_TRANSLATION_TIMEOUT_SECONDS = int(os.getenv("AI_TRANSLATION_TIMEOUT_SECONDS", "45"))
AI_TRANSLATION_MAX_CHARS = int(os.getenv("AI_TRANSLATION_MAX_CHARS", "12000"))
_EMAIL_ALLOWED_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "code", "col",
    "colgroup", "div", "em", "font",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol",
    "p", "pre", "small", "span", "strong", "sub", "sup", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "u", "ul",
]
_EMAIL_ALLOWED_ATTRIBUTES = {
    "*": ["align", "bgcolor", "class", "valign"],
    "a": ["href", "title", "target", "rel", "style"],
    "col": ["span", "width", "style"],
    "colgroup": ["span", "width", "style"],
    "div": ["style"],
    "font": ["color", "size", "face"],
    "img": ["src", "alt", "title", "width", "height", "style"],
    "p": ["style"],
    "span": ["style"],
    "table": ["border", "bordercolor", "cellpadding", "cellspacing", "height", "role", "width", "style"],
    "tbody": ["style"],
    "tfoot": ["style"],
    "thead": ["style"],
    "tr": ["height", "style"],
    "td": ["colspan", "rowspan", "width", "height", "style", "nowrap"],
    "th": ["colspan", "rowspan", "width", "height", "style", "nowrap"],
}
_EMAIL_CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=[
        "background", "background-color", "border", "border-bottom", "border-collapse",
        "border-color", "border-left", "border-right", "border-spacing", "border-style",
        "border-top", "border-width", "color",
        "display", "font", "font-family", "font-size", "font-style", "font-weight",
        "height", "letter-spacing", "line-height", "margin", "margin-bottom",
        "margin-left", "margin-right", "margin-top", "max-width", "min-width",
        "overflow", "overflow-wrap", "overflow-x",
        "padding", "padding-bottom", "padding-left", "padding-right", "padding-top",
        "table-layout", "text-align", "text-decoration", "vertical-align", "white-space",
        "width", "word-break", "word-wrap",
    ]
)


def _require_production_value(name: str, value: str, disallowed: set[str] | None = None):
    if not IS_PRODUCTION:
        return
    disallowed = disallowed or set()
    normalized = (value or "").strip()
    if not normalized or normalized in disallowed:
        raise RuntimeError(f"{name} must be configured for production")


def _settings_key() -> bytes:
    if not CONFIG_ENCRYPTION_KEY:
        raise RuntimeError("CONFIG_ENCRYPTION_KEY is required for persisted settings")
    return hashlib.sha256(CONFIG_ENCRYPTION_KEY.encode("utf-8")).digest()


def _encrypt_setting(value: str) -> dict | None:
    if not value:
        return None
    nonce = os.urandom(12)
    ciphertext = AESGCM(_settings_key()).encrypt(nonce, value.encode("utf-8"), None)
    return {
        "v": 1,
        "alg": "aes-256-gcm",
        "iv": base64.b64encode(nonce).decode("ascii"),
        "data": base64.b64encode(ciphertext).decode("ascii"),
    }


def _decrypt_setting(encrypted: dict | None) -> str:
    if not encrypted or encrypted.get("alg") != "aes-256-gcm":
        return ""
    try:
        nonce = base64.b64decode(encrypted.get("iv", ""))
        ciphertext = base64.b64decode(encrypted.get("data", ""))
        return AESGCM(_settings_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception:
        return ""


def _read_viewer_settings() -> dict:
    if not os.path.exists(VIEWER_SETTINGS_FILE):
        return {}
    try:
        with open(VIEWER_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        app.logger.error(f"读取后台配置失败: {e}", exc_info=True)
        return {}


def _write_viewer_settings(settings: dict):
    settings_dir = os.path.dirname(VIEWER_SETTINGS_FILE)
    if settings_dir:
        os.makedirs(settings_dir, exist_ok=True)
    lock = globals().setdefault("_VIEWER_SETTINGS_WRITE_LOCK", threading.Lock())
    with lock:
        tmp_path = f"{VIEWER_SETTINGS_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, VIEWER_SETTINGS_FILE)


def _get_viewer_secret(name: str) -> str:
    return _decrypt_setting(_read_viewer_settings().get(name))


def _get_admin_password_hash() -> str:
    return _decrypt_setting(_read_viewer_settings().get("admin_password_hash")) or ADMIN_PASSWORD_HASH


def _get_configured_admin_username() -> str:
    settings_username = (_read_viewer_settings().get("admin_username") or "").strip()
    return settings_username or ADMIN_USERNAME


def _get_admin_username() -> str:
    return _get_configured_admin_username() or "admin"


def _admin_auth_enabled() -> bool:
    return bool(_get_admin_password_hash() or ACCESS_PASSWORD)


def _verify_admin_credentials(username: str, password: str) -> bool:
    expected_username = _get_admin_username()
    if username.strip() != expected_username:
        return False
    password_hash = _get_admin_password_hash()
    if password_hash:
        return check_password_hash(password_hash, password)
    return bool(ACCESS_PASSWORD and hmac.compare_digest(password, ACCESS_PASSWORD))


def _get_unified_password() -> str:
    return _get_viewer_secret("unified_password") or UNIFIED_PASSWORD


def _update_viewer_runtime_settings(data: dict) -> dict:
    settings = _read_viewer_settings()
    if "admin_username" in data:
        settings["admin_username"] = (data.get("admin_username") or "").strip()
    if data.get("admin_password"):
        settings["admin_password_hash"] = _encrypt_setting(generate_password_hash(data["admin_password"].strip()))
    if data.get("clear_unified_password"):
        settings.pop("unified_password", None)
    elif data.get("unified_password"):
        settings["unified_password"] = _encrypt_setting(data["unified_password"].strip())
    if data.get("clear_resend_api_key"):
        settings.pop("resend_api_key", None)
    elif data.get("resend_api_key"):
        settings["resend_api_key"] = _encrypt_setting(data["resend_api_key"].strip())
    _write_viewer_settings(settings)
    return settings


def _normalize_ai_provider(provider: str) -> str:
    provider = (provider or "").strip().lower()
    return provider if provider in {"openai", "gemini", "openai_compatible"} else ""


def _normalize_ai_base_url(provider: str, base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if base_url:
        return base_url
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "gemini":
        return "https://generativelanguage.googleapis.com/v1beta"
    return ""


def _safe_ai_channel(channel: dict) -> dict:
    return {
        "id": channel.get("id", ""),
        "name": channel.get("name", ""),
        "provider": channel.get("provider", ""),
        "base_url": channel.get("base_url", ""),
        "models": channel.get("models", []) if isinstance(channel.get("models"), list) else [],
        "updated_at": channel.get("updated_at", ""),
        "created_at": channel.get("created_at", ""),
        "key_configured": bool(_decrypt_setting(channel.get("api_key"))),
    }


def _get_ai_settings() -> dict:
    settings = _read_viewer_settings()
    ai = settings.get("ai", {})
    if not isinstance(ai, dict):
        ai = {}
    channels = ai.get("channels", [])
    if not isinstance(channels, list):
        channels = []
    ai["channels"] = [channel for channel in channels if isinstance(channel, dict)]
    default_model = ai.get("default_model", {})
    ai["default_model"] = default_model if isinstance(default_model, dict) else {}
    return ai


def _write_ai_settings(ai: dict):
    settings = _read_viewer_settings()
    settings["ai"] = ai
    _write_viewer_settings(settings)


def _list_openai_models(base_url: str, api_key: str) -> list[str]:
    resp = http_session.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_response_error(resp, f"模型列表获取失败 HTTP {resp.status_code}"))
    payload = resp.json()
    models = payload.get("data", []) if isinstance(payload, dict) else []
    ids = []
    for item in models:
        model_id = item.get("id") if isinstance(item, dict) else str(item)
        if model_id:
            ids.append(model_id)
    return sorted(set(ids))


def _list_gemini_models(base_url: str, api_key: str) -> list[str]:
    resp = http_session.get(
        f"{base_url.rstrip('/')}/models",
        params={"key": api_key},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_response_error(resp, f"模型列表获取失败 HTTP {resp.status_code}"))
    payload = resp.json()
    models = payload.get("models", []) if isinstance(payload, dict) else []
    ids = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").removeprefix("models/")
        methods = item.get("supportedGenerationMethods") or []
        if name and (not methods or "generateContent" in methods):
            ids.append(name)
    return sorted(set(ids))


def _fetch_ai_models(provider: str, base_url: str, api_key: str) -> list[str]:
    if provider == "gemini":
        return _list_gemini_models(base_url, api_key)
    return _list_openai_models(base_url, api_key)


def _extract_plain_text(value: str) -> str:
    value = value or ""
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip()


def _html_needs_structured_translation(html: str) -> bool:
    """Only preserve HTML when the message contains real data structure, not layout tables."""
    if not html:
        return False
    if re.search(r"(?i)<\s*(ul|ol|li)\b", html):
        return True
    tables = re.findall(r"(?is)<table\b[^>]*>.*?</table>", html)
    for table_html in tables[:8]:
        role = re.search(r'(?i)\brole=["\']([^"\']+)["\']', table_html)
        if role and role.group(1).strip().lower() in {"presentation", "none"}:
            continue
        rows = len(re.findall(r"(?i)<tr\b", table_html))
        cells = len(re.findall(r"(?i)<t[dh]\b", table_html))
        headers = len(re.findall(r"(?i)<th\b", table_html))
        border = re.search(r'(?i)\bborder=["\']([^"\']+)["\']', table_html)
        if headers > 0 or (rows >= 3 and cells >= 8) or (border and border.group(1).strip() not in {"", "0"} and rows >= 2):
            return True
    return False


def _prepare_translation_payload(html_content: str, text_content: str) -> tuple[str, bool, int, bool]:
    """Return content, wants_html, original length, truncated flag."""
    html_content = (html_content or "").strip()
    text_content = (text_content or "").strip()
    wants_html = bool(html_content and _html_needs_structured_translation(html_content))
    if wants_html:
        content = _strip_layout_html_for_translation(html_content)
    else:
        content = _extract_plain_text(text_content or html_content)
    original_length = len(content)
    truncated = False
    if len(content) > AI_TRANSLATION_MAX_CHARS:
        content = content[:AI_TRANSLATION_MAX_CHARS]
        truncated = True
    return content, wants_html, original_length, truncated


def _translation_preview(value: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", (value or "").replace("\r", " ").replace("\n", " ")).strip()
    return text[:limit]


def _normalize_ai_translation(value: str, wants_html: bool) -> str:
    value = (value or "").strip()
    value = re.sub(r"^```(?:html)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    if wants_html:
        return _prepare_html_for_render(value)
    return value


def _call_openai_chat(channel: dict, api_key: str, model: str, content: str, wants_html: bool = False) -> str:
    base_url = channel.get("base_url", "").rstrip("/")
    if not base_url:
        raise RuntimeError("AI 渠道 Base URL 为空，请重新配置渠道")
    system_prompt = (
        "你是专业邮件翻译助手。请把用户提供的邮件 HTML 翻译为中文。"
        "必须保留原始 HTML 结构、表格、段落、列表、链接、按钮文本、图片和行内样式；"
        "只翻译可见文本，不要解释，不要使用 Markdown，不要包裹代码块，只输出翻译后的 HTML。"
        if wants_html else
        "你是专业邮件翻译助手。请只输出中文译文，保留原邮件结构、链接文本和关键信息，不要添加解释。"
    )
    request_started_at = time.time()
    resp = fast_http_session.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        },
        timeout=AI_TRANSLATION_TIMEOUT_SECONDS,
    )
    app.logger.info(
        "AI 请求完成(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s status=%s elapsed_ms=%s",
        base_url,
        model,
        wants_html,
        len(content or ""),
        resp.status_code,
        int((time.time() - request_started_at) * 1000),
    )
    if resp.status_code >= 400:
        raise RuntimeError(_response_error(resp, f"翻译失败 HTTP {resp.status_code}"))
    try:
        payload = resp.json()
    except ValueError as exc:
        snippet = re.sub(r"\s+", " ", (resp.text or "").replace("\r", " ").replace("\n", " ")).strip()[:500]
        raise RuntimeError(f"AI 服务返回了非 JSON 内容: {snippet}") from exc
    return (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


def _call_gemini(channel: dict, api_key: str, model: str, content: str, wants_html: bool = False) -> str:
    base_url = channel.get("base_url", "").rstrip("/")
    if not base_url:
        raise RuntimeError("AI 渠道 Base URL 为空，请重新配置渠道")
    model_name = model if model.startswith("models/") else f"models/{model}"
    prompt = (
        "请把下面邮件 HTML 翻译为中文。必须保留原始 HTML 结构、表格、段落、列表、链接、按钮文本、图片和行内样式；"
        "只翻译可见文本，不要解释，不要使用 Markdown，不要包裹代码块，只输出翻译后的 HTML。\n\n"
        if wants_html else
        "请把下面邮件翻译为中文。只输出中文译文，保留结构、链接文本和关键信息，不要添加解释。\n\n"
    )
    request_started_at = time.time()
    resp = fast_http_session.post(
        f"{base_url}/{model_name}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{
                "role": "user",
                "parts": [{
                    "text": prompt + content
                }],
            }],
            "generationConfig": {"temperature": 0.2},
        },
        timeout=AI_TRANSLATION_TIMEOUT_SECONDS,
    )
    app.logger.info(
        "AI 请求完成(gemini): base_url=%s model=%s wants_html=%s input_chars=%s status=%s elapsed_ms=%s",
        base_url,
        model_name,
        wants_html,
        len(content or ""),
        resp.status_code,
        int((time.time() - request_started_at) * 1000),
    )
    if resp.status_code >= 400:
        raise RuntimeError(_response_error(resp, f"翻译失败 HTTP {resp.status_code}"))
    try:
        payload = resp.json()
    except ValueError as exc:
        snippet = re.sub(r"\s+", " ", (resp.text or "").replace("\r", " ").replace("\n", " ")).strip()[:500]
        raise RuntimeError(f"AI 服务返回了非 JSON 内容: {snippet}") from exc
    candidates = payload.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    return "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


def _normalize_mailbox_address(address: str) -> str:
    address = (address or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", address):
        return ""
    return address


def _normalize_display_name(value: str, fallback: str = "") -> str:
    value = (value or "").strip()
    if len(value) > 80:
        value = value[:80]
    return value or fallback


def _normalize_account_group(value: str) -> str:
    value = (value or "").strip()
    if len(value) > 40:
        value = value[:40]
    return value


def _normalize_account_order(order: list) -> list[str]:
    if not isinstance(order, list):
        return []
    normalized = []
    seen = set()
    for item in order:
        key = str(item or "").strip()
        if key.startswith("local:"):
            address = _normalize_mailbox_address(key.removeprefix("local:"))
            key = f"local:{address}" if address else ""
        elif key.startswith("localEmail:"):
            address = _normalize_mailbox_address(key.removeprefix("localEmail:"))
            key = f"localEmail:{address}" if address else ""
        elif key.startswith("external:"):
            account_id = key.removeprefix("external:").strip()
            key = f"external:{account_id}" if re.match(r"^[\w.-]{1,80}$", account_id) else ""
        elif key.startswith("externalEmail:"):
            email = _normalize_mailbox_address(key.removeprefix("externalEmail:"))
            key = f"externalEmail:{email}" if email else ""
        else:
            key = ""
        if key and key not in seen:
            normalized.append(key)
            seen.add(key)
    return normalized


_require_production_value("SECRET_KEY", app.secret_key, {"mail-viewer-secret-key-change-me"})
if IS_PRODUCTION and not _get_admin_password_hash() and not ACCESS_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD_HASH or ACCESS_PASSWORD must be configured for production")
_require_production_value("DUCKMAIL_BASE_URL", DUCKMAIL_BASE_URL, {"http://161.33.195.3:8080"})
_require_production_value("DUCKMAIL_API_KEY", DUCKMAIL_API_KEY)
_require_production_value("IMAP_MAIL_BASE_URL", IMAP_MAIL_BASE_URL)
_require_production_value("CONFIG_ENCRYPTION_KEY", CONFIG_ENCRYPTION_KEY, {"mail-viewer-secret-key-change-me"})


_LOGIN_BUCKETS: dict[str, dict] = {}
CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_ACTIONS = {
    "save_settings",
    "delete_mailbox",
    "delete_external_account",
    "permanent_delete",
    "create_device_token",
    "revoke_device_token",
    "revoke_session",
}
SENSITIVE_CONFIRM_TTL_SECONDS = 10 * 60
DEVICE_TOKEN_PREFIX = "memail_dev_"
SYNC_EVENT_LIMIT = 200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _request_user_agent() -> str:
    return (request.headers.get("User-Agent", "") or "")[:240]


def _current_session_id() -> str:
    sid = session.get("session_id")
    if not sid:
        sid = secrets.token_urlsafe(18)
        session["session_id"] = sid
    return sid


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(18)}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _settings_list(settings: dict, key: str) -> list:
    value = settings.get(key, [])
    return value if isinstance(value, list) else []


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _touch_sync_event(settings: dict, event_type: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    seq = _safe_int(settings.get("sync_seq"), 0) + 1
    settings["sync_seq"] = seq
    event = {
        "seq": seq,
        "id": _new_id("evt_"),
        "type": event_type,
        "payload": payload,
        "created_at": _iso_now(),
    }
    events = _settings_list(settings, "sync_events")
    events.append(event)
    settings["sync_events"] = events[-1000:]
    return event


def _append_audit(settings: dict, action: str, detail: dict | None = None, success: bool = True):
    entry = {
        "id": _new_id("aud_"),
        "action": action,
        "detail": detail or {},
        "success": bool(success),
        "ip": _client_ip(),
        "user_agent": _request_user_agent(),
        "session_id": session.get("session_id", ""),
        "username": session.get("username", ""),
        "created_at": _iso_now(),
    }
    audit = _settings_list(settings, "audit_logs")
    audit.append(entry)
    settings["audit_logs"] = audit[-1000:]
    _touch_sync_event(settings, "audit.created", {"id": entry["id"], "action": action})


def _register_session(settings: dict):
    sid = _current_session_id()
    sessions = _settings_list(settings, "sessions")
    now = _iso_now()
    existing = next((item for item in sessions if item.get("id") == sid), None)
    if existing:
        existing.update({
            "last_seen": now,
            "ip": _client_ip(),
            "user_agent": _request_user_agent(),
            "revoked": False,
        })
    else:
        sessions.append({
            "id": sid,
            "username": session.get("username", ""),
            "ip": _client_ip(),
            "user_agent": _request_user_agent(),
            "created_at": now,
            "last_seen": now,
            "revoked": False,
        })
    settings["sessions"] = sessions[-200:]
    _append_audit(settings, "login.success", {"session_id": sid})
    _touch_sync_event(settings, "security.session.created", {"session_id": sid})


def _update_session_seen(settings: dict):
    sid = session.get("session_id")
    if not sid:
        return
    sessions = _settings_list(settings, "sessions")
    existing = next((item for item in sessions if item.get("id") == sid), None)
    if existing:
        existing["last_seen"] = _iso_now()
        existing["ip"] = _client_ip()
        settings["sessions"] = sessions


def _session_revoked(settings: dict) -> bool:
    sid = session.get("session_id")
    if not sid:
        return False
    return any(item.get("id") == sid and item.get("revoked") for item in _settings_list(settings, "sessions"))


def _totp_secret() -> str:
    return _get_viewer_secret("totp_secret")


def _totp_enabled(settings: dict | None = None) -> bool:
    settings = settings or _read_viewer_settings()
    return bool(settings.get("totp_enabled") and _decrypt_setting(settings.get("totp_secret")))


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def _verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    normalized = re.sub(r"\s+", "", code or "")
    if not re.fullmatch(r"\d{6}", normalized):
        return False
    counter = int(time.time() // 30)
    return any(hmac.compare_digest(_hotp(secret, counter + delta), normalized) for delta in range(-window, window + 1))


def _require_sensitive_confirmation(action: str):
    if action not in SENSITIVE_ACTIONS:
        return None
    confirmed = session.get("sensitive_confirmed", {})
    if not isinstance(confirmed, dict):
        confirmed = {}
    last = float(confirmed.get(action, 0) or confirmed.get("*", 0) or 0)
    if time.time() - last <= SENSITIVE_CONFIRM_TTL_SECONDS:
        return None
    return jsonify({"success": False, "message": "需要二次确认", "require_confirmation": True, "action": action}), 403


def _confirm_sensitive_action(action: str):
    confirmed = session.get("sensitive_confirmed", {})
    if not isinstance(confirmed, dict):
        confirmed = {}
    now = time.time()
    confirmed[action] = now
    confirmed["*"] = now
    session["sensitive_confirmed"] = confirmed


def _extract_device_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Device-Token", "").strip()


def _public_draft(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "account_type": item.get("account_type", "local"),
        "account_id": item.get("account_id", ""),
        "from_email": item.get("from_email", ""),
        "from_name": item.get("from_name", ""),
        "to": item.get("to", ""),
        "subject": item.get("subject", ""),
        "text": item.get("text", ""),
        "html": item.get("html", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "version": item.get("version", 1),
    }


def _public_outbox(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "account_type": item.get("account_type", "local"),
        "account_id": item.get("account_id", ""),
        "from_email": item.get("from_email", ""),
        "from_name": item.get("from_name", ""),
        "to": item.get("to", ""),
        "subject": item.get("subject", ""),
        "text": item.get("text", ""),
        "html": item.get("html", ""),
        "status": item.get("status", "failed"),
        "error": item.get("error", ""),
        "attempts": item.get("attempts", 0),
        "last_attempt_at": item.get("last_attempt_at", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def _store_outbox_failure(settings: dict, data: dict, message: str, account_type: str = "local") -> dict:
    now = _iso_now()
    item = {
        "id": _new_id("out_"),
        "account_type": account_type,
        "account_id": data.get("account_id") or data.get("from_email") or "",
        "from_email": data.get("from_email", ""),
        "from_name": data.get("from_name", ""),
        "to": data.get("to", ""),
        "subject": data.get("subject", ""),
        "text": data.get("text", ""),
        "html": data.get("html", ""),
        "reply_to": data.get("reply_to", ""),
        "status": "failed",
        "error": message,
        "attempts": 1,
        "last_attempt_at": now,
        "created_at": now,
        "updated_at": now,
    }
    outbox = _settings_list(settings, "outbox")
    outbox.append(item)
    settings["outbox"] = outbox[-500:]
    _touch_sync_event(settings, "outbox.failed", {"id": item["id"]})
    return item


def _update_outbox_item(settings: dict, item_id: str, status: str, error: str = ""):
    if not item_id:
        return
    outbox = _settings_list(settings, "outbox")
    for item in outbox:
        if item.get("id") == item_id:
            item["status"] = status
            item["error"] = error
            item["updated_at"] = _iso_now()
            if status == "failed":
                item["last_attempt_at"] = item["updated_at"]
            break
    settings["outbox"] = outbox


def _message_meta_key(account_type: str, account_id: str, folder: str, message_id: str) -> str:
    return "|".join([
        account_type.strip().lower() or "local",
        account_id.strip().lower(),
        folder.strip() or "INBOX",
        str(message_id).strip(),
    ])


def _public_message_meta(item: dict) -> dict:
    return {
        "favorite": bool(item.get("favorite")),
        "pinned": bool(item.get("pinned")),
        "color": item.get("color", ""),
        "updated_at": item.get("updated_at", ""),
    }


def _get_message_meta(settings: dict, account_type: str, account_id: str, folder: str, message_id: str) -> dict:
    key = _message_meta_key(account_type, account_id, folder, message_id)
    meta = settings.get("message_meta", {})
    if not isinstance(meta, dict):
        return {}
    item = meta.get(key, {})
    return item if isinstance(item, dict) else {}


def _merge_message_meta(settings: dict, account_type: str, account_id: str, folder: str, message_id: str, patch: dict) -> dict:
    key = _message_meta_key(account_type, account_id, folder, message_id)
    meta = settings.get("message_meta", {})
    if not isinstance(meta, dict):
        meta = {}
    item = meta.get(key, {})
    if not isinstance(item, dict):
        item = {}
    item.update({
        "account_type": account_type,
        "account_id": account_id,
        "folder": folder,
        "message_id": str(message_id),
        "updated_at": _iso_now(),
    })
    for name in ("favorite", "pinned"):
        if name in patch:
            item[name] = bool(patch.get(name))
    if "color" in patch:
        color = str(patch.get("color") or "").strip().lower()
        item["color"] = color if re.fullmatch(r"(red|orange|yellow|green|blue|purple|gray)", color) else ""
    if not item.get("favorite") and not item.get("pinned") and not item.get("color"):
        meta.pop(key, None)
        item = {}
    else:
        meta[key] = item
    settings["message_meta"] = dict(list(meta.items())[-5000:])
    return item


def _attach_message_meta(settings: dict, messages: list, account_type: str, account_id: str, folder: str):
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        message_id = str(msg.get("id") or msg.get("msgid") or msg.get("uid") or "")
        if not message_id:
            continue
        msg["meta"] = _public_message_meta(_get_message_meta(settings, account_type, account_id, folder, message_id))
    return messages


def _normalize_keyword_rule(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name", "")).strip()
    raw_keywords = item.get("keywords", [])
    if isinstance(raw_keywords, str):
        raw_keywords = re.split(r"[\n,，]+", raw_keywords)
    keywords = [
        str(keyword).strip()
        for keyword in raw_keywords
        if str(keyword).strip()
    ]
    if not name or not keywords:
        return None
    scope_type = str(item.get("scope_type", "all")).strip().lower()
    if scope_type not in {"all", "group", "accounts"}:
        scope_type = "all"
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        fields = []
    normalized_fields = [
        field for field in fields
        if field in {"subject", "from", "to", "intro", "body"}
    ] or ["subject", "from", "intro"]
    match_mode = str(item.get("match_mode", "any")).strip().lower()
    if match_mode not in {"any", "all"}:
        match_mode = "any"
    raw_scope_accounts = item.get("scope_accounts", [])
    if isinstance(raw_scope_accounts, str):
        raw_scope_accounts = [part.strip() for part in raw_scope_accounts.split(",")]
    raw_enabled = item.get("enabled", True)
    enabled = raw_enabled
    if isinstance(raw_enabled, str):
        enabled = raw_enabled.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return {
        "id": str(item.get("id") or _new_id("kw_")),
        "name": name[:80],
        "scope_type": scope_type,
        "scope_group": _normalize_account_group(item.get("scope_group", "")),
        "scope_accounts": [
            str(account).strip()
            for account in raw_scope_accounts
            if str(account).strip()
        ][:100],
        "keywords": keywords[:30],
        "match_mode": match_mode,
        "fields": normalized_fields,
        "enabled": bool(enabled),
        "created_at": item.get("created_at", _iso_now()),
        "updated_at": item.get("updated_at", _iso_now()),
    }


def _public_keyword_rule(item: dict) -> dict:
    normalized = _normalize_keyword_rule(item) or {}
    return {
        "id": normalized.get("id", ""),
        "name": normalized.get("name", ""),
        "scope_type": normalized.get("scope_type", "all"),
        "scope_group": normalized.get("scope_group", ""),
        "scope_accounts": normalized.get("scope_accounts", []),
        "keywords": normalized.get("keywords", []),
        "match_mode": normalized.get("match_mode", "any"),
        "fields": normalized.get("fields", ["subject", "from", "intro"]),
        "enabled": bool(normalized.get("enabled", True)),
        "created_at": normalized.get("created_at", ""),
        "updated_at": normalized.get("updated_at", ""),
    }


def _device_auth() -> dict | None:
    token = _extract_device_token()
    if not token:
        return None
    settings = _read_viewer_settings()
    token_hash = _hash_token(token)
    now = _iso_now()
    for item in _settings_list(settings, "device_tokens"):
        if item.get("token_hash") == token_hash and not item.get("revoked"):
            item["last_seen"] = now
            item["last_ip"] = _client_ip()
            _write_viewer_settings(settings)
            return {"id": item.get("id"), "name": item.get("name", ""), "scopes": item.get("scopes", [])}
    return None


def device_or_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = _device_auth()
        if auth:
            request.device_auth = auth
            return f(*args, **kwargs)
        return login_required(f)(*args, **kwargs)
    return decorated_function


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _login_bucket_key(username: str) -> str:
    return f"{_client_ip()}:{username.strip().lower() or '-'}"


def _login_bucket(username: str) -> dict:
    key = _login_bucket_key(username)
    bucket = _LOGIN_BUCKETS.setdefault(key, {"attempts": 0, "locked_until": 0.0})
    now = time.time()
    if bucket.get("locked_until", 0) <= now and bucket.get("attempts", 0) <= 0:
        bucket["locked_until"] = 0.0
    return bucket


def _is_login_locked(username: str) -> bool:
    return _login_bucket(username).get("locked_until", 0.0) > time.time()


def _register_login_failure(username: str):
    bucket = _login_bucket(username)
    bucket["attempts"] = int(bucket.get("attempts", 0)) + 1
    if bucket["attempts"] >= LOGIN_MAX_ATTEMPTS:
        bucket["locked_until"] = time.time() + LOGIN_LOCK_MINUTES * 60


def _clear_login_failures(username: str):
    _LOGIN_BUCKETS.pop(_login_bucket_key(username), None)


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _wants_json_response() -> bool:
    return request.is_json or request.path.startswith("/api/") or request.path.startswith("/imap/api/")


@app.before_request
def enforce_session_security():
    if request.endpoint in {"login_page", "static", "oauth_callback_compat", "imap_oauth_callback"}:
        return None
    if not _admin_auth_enabled():
        return None
    if session.get("authenticated"):
        settings = _read_viewer_settings()
        if _session_revoked(settings):
            session.clear()
            if _wants_json_response():
                return jsonify({"success": False, "message": "当前会话已被管理员踢出"}), 401
            return redirect(url_for("login_page"))
        now = time.time()
        last_seen = float(session.get("last_seen", now))
        if SESSION_TIMEOUT_MINUTES > 0 and now - last_seen > SESSION_TIMEOUT_MINUTES * 60:
            session.clear()
            if _wants_json_response():
                return jsonify({"success": False, "message": "登录已过期，请重新登录"}), 401
            return redirect(url_for("login_page"))
        session["last_seen"] = now
        session.permanent = True
        if now - float(session.get("session_seen_saved_at", 0) or 0) > 60:
            _update_session_seen(settings)
            _write_viewer_settings(settings)
            session["session_seen_saved_at"] = now
    if request.method in CSRF_METHODS and session.get("authenticated"):
        sent_token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not sent_token or not hmac.compare_digest(str(sent_token), str(session.get("csrf_token", ""))):
            return jsonify({"success": False, "message": "CSRF 校验失败，请刷新页面后重试"}), 403
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if _admin_auth_enabled() and not session.get("authenticated"):
            if _wants_json_response():
                return jsonify({"success": False, "message": "未授权访问"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function

# 创建带重试的 HTTP session
http_session = requests.Session()
adapter = requests.adapters.HTTPAdapter(max_retries=3)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)
fast_http_session = requests.Session()
fast_http_adapter = requests.adapters.HTTPAdapter(max_retries=0)
fast_http_session.mount("http://", fast_http_adapter)
fast_http_session.mount("https://", fast_http_adapter)


def _normalize_remote_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _is_public_hostname(hostname: str) -> bool:
    if not hostname:
        return False
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False

    has_public_ip = False
    for _, _, _, _, sockaddr in addr_infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if any([
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        ]):
            return False
        has_public_ip = True
    return has_public_ip


def _is_proxyable_image_url(url: str) -> bool:
    parsed = urlparse(_normalize_remote_url(url))
    return parsed.scheme in {"http", "https"} and _is_public_hostname(parsed.hostname or "")


def _sanitize_email_html(html: str) -> str:
    html = (html or "").strip()
    if not html:
        return ""
    body_match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        html = body_match.group(1)
    cleaned = bleach.clean(
        html,
        tags=_EMAIL_ALLOWED_TAGS,
        attributes=_EMAIL_ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto", "cid", "data"},
        strip=True,
        css_sanitizer=_EMAIL_CSS_SANITIZER,
    )
    return cleaned.strip()


def _strip_layout_html_for_translation(html: str) -> str:
    html = _sanitize_email_html(html)
    if not html:
        return ""
    html = re.sub(r"(?is)<img\b[^>]*>", " ", html)
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)</?(html|head|body|meta|title)\b[^>]*>", " ", html)
    return html.strip()


def _prepare_html_for_render(html: str) -> str:
    return _rewrite_html_images(_sanitize_email_html(html))


def _rewrite_imap_html(html: str) -> str:
    rewritten = html.replace("'/api/", "'/imap/api/").replace('"/api/', '"/imap/api/')
    rewritten = rewritten.replace("fetch(url, opts)", "fetch(url, opts)")
    return rewritten


def _proxy_imap_response(subpath: str = ""):
    target = urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", subpath.lstrip("/"))
    is_api_request = subpath.strip("/").startswith("api/")
    headers = {}
    for key, value in request.headers.items():
        key_lower = key.lower()
        if key_lower in {"host", "content-length", "cookie"}:
            continue
        if key_lower in {"accept", "content-type", "x-requested-with"}:
            headers[key] = value
    body = None if request.method in {"GET", "HEAD"} else request.get_data()
    try:
        resp = http_session.request(
            method=request.method,
            url=target,
            params=request.args,
            data=body,
            headers=headers,
            timeout=60,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        app.logger.warning(f"IMAP 代理请求失败: {e}", exc_info=True)
        response = jsonify({
            "error": "IMAP 服务暂时不可用，请稍后重试",
            "detail": str(e),
        })
        response.headers["Cache-Control"] = "no-store"
        return response, 502

    content_type = resp.headers.get("Content-Type", "")
    if is_api_request and "text/html" in content_type and resp.status_code >= 400:
        message = _extract_plain_text(resp.text) or f"IMAP 服务异常 HTTP {resp.status_code}"
        response = jsonify({"error": message})
        response.headers["Cache-Control"] = "no-store"
        return response, resp.status_code

    payload = resp.content
    if "text/html" in content_type:
        payload = _rewrite_imap_html(resp.text).encode(resp.encoding or "utf-8")
    proxied = Response(payload, status=resp.status_code, content_type=content_type or None)
    for header in ["Content-Disposition", "Cache-Control", "Location"]:
        if header in resp.headers:
            value = resp.headers[header]
            if header == "Location" and value.startswith("/"):
                value = "/imap" + value
            proxied.headers[header] = value
    if is_api_request:
        proxied.headers["Cache-Control"] = "no-store"
    return proxied


def _rewrite_html_images(html: str) -> str:
    if not html or "<img" not in html.lower():
        return html

    def _replace(match):
        prefix, src, suffix = match.groups()
        normalized = _normalize_remote_url(src)
        if not _is_proxyable_image_url(normalized):
            return match.group(0)
        proxied = url_for("image_proxy", url=normalized)
        return f"{prefix}{proxied}{suffix}"

    return re.sub(r'(<img\b[^>]*?\bsrc=["\'])([^"\']+)(["\'])', _replace, html, flags=re.IGNORECASE)


def _get_mail_token(email: str, password: str = "") -> tuple:
    """获取邮件服务 Token，返回 (token, error_response)"""
    password = password or _get_unified_password()
    if not password:
        return None, ("未配置邮箱统一密码", 500)
    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        token_resp = http_session.post(
            f"{base_url}/token",
            json={"address": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if token_resp.status_code != 200:
            return None, ("登录失败", token_resp.status_code)
        token = token_resp.json().get("token")
        return token, None
    except Exception as e:
        app.logger.error(f"获取 mail token 失败: {e}", exc_info=True)
        return None, ("连接邮件服务失败", 500)


def _extract_api_error(resp, fallback: str = "操作失败") -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data.get("detail") or data.get("message") or data.get("hydra:description") or fallback
    except Exception:
        pass
    return fallback


def _is_managed_sender(address: str) -> bool:
    """只有能用统一密码登录的本地账户才允许作为 Web 发件人。"""
    unified_password = _get_unified_password()
    if not address or not unified_password:
        return False
    token, _ = _get_mail_token(address, unified_password)
    return bool(token)


def _format_attachments(detail: dict) -> list:
    attachments = detail.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    if not attachments and detail.get("hasAttachments"):
        attachments = [{"index": 0, "filename": "attachment", "size": 0}]
    normalized = []
    for index, item in enumerate(attachments):
        if isinstance(item, dict):
            normalized.append({
                "index": item.get("index", index),
                "id": item.get("id") or item.get("attachment_id") or item.get("contentId") or "",
                "filename": item.get("filename") or item.get("name") or f"attachment_{index}",
                "size": item.get("size") or 0,
                "contentType": item.get("contentType") or item.get("content_type") or "",
            })
        else:
            normalized.append({"index": index, "id": "", "filename": str(item), "size": 0, "contentType": ""})
    return normalized


def _find_attachment_download_url(base_url: str, message_id: str, attachment_id: str, headers: dict):
    quoted_id = quote(attachment_id, safe="")
    candidate_paths = [
        f"/messages/{message_id}/attachments/{quoted_id}",
        f"/messages/{message_id}/attachment/{quoted_id}",
        f"/messages/{message_id}/attachments?index={quoted_id}",
    ]

    for path in candidate_paths:
        url = f"{base_url}{path}"
        try:
            resp = http_session.get(url, headers=headers, stream=True, timeout=30)
        except Exception:
            continue
        if resp.status_code == 200:
            return url, resp
        resp.close()
    return None, None


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """登录页面"""
    if not _admin_auth_enabled():
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        totp_code = request.form.get("totp_code", "")
        if _is_login_locked(username):
            return render_template("login.html", error="登录失败次数过多，请稍后再试", username=username)
        if _verify_admin_credentials(username, password):
            settings = _read_viewer_settings()
            if _totp_enabled(settings) and not _verify_totp_code(_decrypt_setting(settings.get("totp_secret")), totp_code):
                _register_login_failure(username)
                _append_audit(settings, "login.totp_failed", {"username": username.strip()}, success=False)
                _write_viewer_settings(settings)
                return render_template("login.html", error="二次验证码错误", username=username, totp_required=True)
            session.clear()
            session["authenticated"] = True
            session["username"] = _get_admin_username()
            session["session_id"] = secrets.token_urlsafe(18)
            session["login_at"] = time.time()
            session["last_seen"] = time.time()
            session["csrf_token"] = secrets.token_urlsafe(32)
            session.permanent = True
            _clear_login_failures(username)
            _register_session(settings)
            _write_viewer_settings(settings)
            return redirect(url_for("index"))
        _register_login_failure(username)
        settings = _read_viewer_settings()
        _append_audit(settings, "login.failed", {"username": username.strip()}, success=False)
        _write_viewer_settings(settings)
        return render_template("login.html", error="用户名或密码错误", username=username)

    return render_template("login.html", error=None, username="", totp_required=_totp_enabled())


@app.route("/logout")
def logout():
    settings = _read_viewer_settings()
    _append_audit(settings, "logout", {"session_id": session.get("session_id", "")})
    _write_viewer_settings(settings)
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    return render_template("index.html", csrf_token=_csrf_token())


@app.route("/imap")
@login_required
def imap_root():
    return redirect("/imap/")


@app.route("/imap/", defaults={"subpath": ""}, methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
@app.route("/imap/<path:subpath>", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
@login_required
def imap_proxy(subpath: str):
    if request.method == "DELETE" and re.fullmatch(r"api/accounts/[^/]+", subpath.strip("/")):
        confirm = _require_sensitive_confirmation("delete_external_account")
        if confirm:
            return confirm
    return _proxy_imap_response(subpath)


@app.route("/api/oauth/<provider>/callback", methods=["GET"])
def oauth_callback_compat(provider: str):
    """兼容历史/误配置的 OAuth 回调路径，统一转发到 IMAP bridge。"""
    if provider not in {"gmail", "outlook"}:
        return jsonify({"success": False, "message": "不支持的 OAuth provider"}), 404
    return _proxy_imap_response(f"api/oauth/{provider}/callback")


@app.route("/imap/api/oauth/<provider>/callback", methods=["GET"])
def imap_oauth_callback(provider: str):
    """OAuth provider 回跳入口；IMAP bridge 会校验 state 并完成 token 交换。"""
    if provider not in {"gmail", "outlook"}:
        return jsonify({"success": False, "message": "不支持的 OAuth provider"}), 404
    return _proxy_imap_response(f"api/oauth/{provider}/callback")


@app.route("/api/image-proxy")
@login_required
def image_proxy():
    """服务端代理远程图片，避免客户端地区/网络限制导致邮件图片加载失败。"""
    source_url = _normalize_remote_url(request.args.get("url", ""))
    if not _is_proxyable_image_url(source_url):
        return jsonify({"success": False, "message": "非法图片地址"}), 400

    try:
        resp = fast_http_session.get(
            source_url,
            timeout=(IMAGE_PROXY_CONNECT_TIMEOUT_SECONDS, IMAGE_PROXY_READ_TIMEOUT_SECONDS),
            stream=True,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 mail-viewer-image-proxy",
                "Accept": "image/*,*/*;q=0.8",
            },
        )
    except requests.RequestException as e:
        app.logger.warning("图片代理请求失败: url=%s error=%s", source_url, e)
        return jsonify({"success": False, "message": "图片加载超时或远程不可用"}), 504

    final_url = _normalize_remote_url(resp.url)
    if not resp.ok or not _is_proxyable_image_url(final_url):
        resp.close()
        return jsonify({"success": False, "message": "图片加载失败"}), 502

    content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
        resp.close()
        return jsonify({"success": False, "message": "远程资源不是图片"}), 415

    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_IMAGE_PROXY_BYTES:
        resp.close()
        return jsonify({"success": False, "message": "图片过大"}), 413

    chunks = []
    total = 0
    try:
        for chunk in resp.iter_content(65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_PROXY_BYTES:
                return jsonify({"success": False, "message": "图片过大"}), 413
            chunks.append(chunk)
    finally:
        resp.close()

    proxied_resp = Response(b"".join(chunks), mimetype=content_type)
    proxied_resp.headers["Cache-Control"] = "public, max-age=3600"
    return proxied_resp


@app.route("/api/inbox/query", methods=["POST"])
@login_required
def inbox_query():
    """通用收件箱查询 - 自动创建邮箱（如不存在）"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    offset = int(data.get("offset", 0))
    limit = int(data.get("limit", 30))
    unread_only = bool(data.get("unread_only") or data.get("unreadOnly"))

    if not email:
        return jsonify({"success": False, "message": "请输入邮箱", "messages": []})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")

    try:
        # 尝试登录获取 Token
        token_resp = http_session.post(
            f"{base_url}/token",
            json={"address": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=30
        )

        # 如果登录失败（邮箱不存在），尝试创建
        if token_resp.status_code != 200:
            if not DUCKMAIL_API_KEY:
                return jsonify({"success": False, "message": "邮箱不存在且未配置 API Key，无法自动创建", "messages": []})

            create_headers = {
                "Authorization": f"Bearer {DUCKMAIL_API_KEY}",
                "Content-Type": "application/json",
            }
            create_resp = http_session.post(
                f"{base_url}/accounts",
                json={"address": email, "password": password},
                headers=create_headers,
                timeout=30
            )

            if create_resp.status_code not in [200, 201]:
                error_msg = "邮箱创建失败"
                try:
                    error_data = create_resp.json()
                    if "violations" in error_data:
                        error_msg = error_data["violations"][0].get("message", error_msg)
                    elif "hydra:description" in error_data:
                        error_msg = error_data["hydra:description"]
                except Exception:
                    pass
                return jsonify({"success": False, "message": error_msg, "messages": []})

            # 创建成功后重新登录
            token_resp = http_session.post(
                f"{base_url}/token",
                json={"address": email, "password": password},
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            if token_resp.status_code != 200:
                return jsonify({"success": False, "message": "登录失败", "messages": []})

        token = token_resp.json().get("token")

        # 获取邮件列表（带分页参数）
        mail_resp = http_session.get(
            f"{base_url}/messages",
            params={"offset": offset, "limit": limit, **({"unread": "true"} if unread_only else {})},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30
        )

        if mail_resp.status_code != 200:
            return jsonify({"success": False, "message": "获取邮件失败", "messages": []})

        resp_data = mail_resp.json()
        messages = resp_data.get("hydra:member", []) if isinstance(resp_data, dict) else resp_data
        total = resp_data.get("hydra:totalItems", len(messages)) if isinstance(resp_data, dict) else len(messages)

        # 过滤：只保留发给当前查询邮箱的邮件（DuckMail 会返回同前缀所有域名的邮件）
        filtered = []
        for msg in messages:
            to_list = msg.get("to", [])
            if any(r.get("address", "").lower() == email.lower() for r in to_list):
                filtered.append(msg)
        messages = filtered

        # 为每封邮件提取验证码
        for msg in messages:
            subject = msg.get("subject", "")
            intro = msg.get("intro", "")
            text = f"{subject} {intro}"
            code_match = re.search(r"\b(\d{6})\b", text)
            msg["extracted_code"] = code_match.group(1) if code_match else None
        settings = _read_viewer_settings()
        _attach_message_meta(settings, messages, "local", email, "inbox")

        return jsonify({
            "success": True,
            "messages": messages,
            "total": total,
            "offset": offset,
            "limit": limit,
        })

    except Exception as e:
        app.logger.error(f"收件箱查询失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试", "messages": []})


# ---- 域名管理 API（代理到 mail-server /admin/domains） ----

@app.route("/api/domains", methods=["GET"])
@login_required
def list_domains():
    """获取域名列表"""
    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        resp = http_session.get(
            f"{base_url}/domains",
            timeout=30,
        )
        if resp.status_code != 200:
            return jsonify({"success": False, "message": f"获取域名失败: {resp.status_code}"}), resp.status_code
        payload = resp.json()
        domains = payload.get("hydra:member", []) if isinstance(payload, dict) else []
        normalized = [
            {
                "domain": item.get("domain", ""),
                "is_active": item.get("isActive", True),
            }
            for item in domains
        ]
        return jsonify({"success": True, "domains": normalized})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/domains", methods=["POST"])
@login_required
def add_domain():
    """添加新域名"""
    data = request.json or {}
    domain = data.get("domain", "").strip().lower()
    if not domain:
        return jsonify({"success": False, "message": "域名不能为空"})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        resp = http_session.post(
            f"{base_url}/admin/domains",
            json={"domain": domain},
            headers={
                "Authorization": f"Bearer {DUCKMAIL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return jsonify({"success": True, **resp.json()})
        else:
            detail = resp.json().get("detail", "添加失败") if resp.headers.get("content-type", "").startswith("application/json") else "添加失败"
            return jsonify({"success": False, "message": detail}), resp.status_code
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/domains/<domain>", methods=["DELETE"])
@login_required
def delete_domain(domain):
    """删除（停用）域名"""
    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        resp = http_session.delete(
            f"{base_url}/admin/domains/{domain}",
            headers={"Authorization": f"Bearer {DUCKMAIL_API_KEY}"},
            timeout=30,
        )
        if resp.status_code == 200:
            return jsonify({"success": True, **resp.json()})
        else:
            detail = resp.json().get("detail", "删除失败") if resp.headers.get("content-type", "").startswith("application/json") else "删除失败"
            return jsonify({"success": False, "message": detail}), resp.status_code
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ---- 运行配置 API（保存到持久化卷；敏感字段加密） ----

def _response_error(resp, default: str) -> str:
    try:
        payload = resp.json()
    except Exception:
        text = (resp.text or "").replace("\r", " ").replace("\n", " ")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return f"{default}: {text[:500]}" if text else default
    if isinstance(payload, dict):
        detail = payload.get("message") or payload.get("error") or payload.get("detail")
        if detail:
            return f"{default}: {detail}"
        return default
    return default


def _get_imap_runtime_settings() -> tuple[dict, str | None]:
    target = urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", "api/settings")
    try:
        resp = http_session.get(target, timeout=20)
    except Exception as e:
        return {}, str(e)
    if resp.status_code != 200:
        return {}, _response_error(resp, f"IMAP 设置服务返回 HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except Exception:
        return {}, "IMAP 设置服务返回了无效响应"
    if not payload.get("success", True):
        return {}, payload.get("message") or payload.get("error") or "读取 IMAP 设置失败"
    return payload.get("settings", {}), None


def _save_imap_runtime_settings(data: dict) -> tuple[dict, str | None]:
    target = urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", "api/settings")
    try:
        resp = http_session.post(target, json=data, timeout=20)
    except Exception as e:
        return {}, str(e)
    if resp.status_code != 200:
        return {}, _response_error(resp, f"IMAP 设置服务返回 HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except Exception:
        return {}, "IMAP 设置服务返回了无效响应"
    if not payload.get("success", True):
        return {}, payload.get("message") or payload.get("error") or "保存 IMAP 设置失败"
    return payload.get("settings", {}), None


@app.route("/api/settings", methods=["GET"])
@login_required
def get_runtime_settings():
    """读取后台运行配置。敏感值只返回是否已配置，不回显原文。"""
    local_settings = _read_viewer_settings()
    resend_runtime = _decrypt_setting(local_settings.get("resend_api_key"))
    imap_settings, imap_error = _get_imap_runtime_settings()
    return jsonify({
        "success": True,
        "settings": {
            "admin_username": _get_admin_username(),
            "admin_username_source": "runtime" if (local_settings.get("admin_username") or "").strip() else ("env" if ADMIN_USERNAME else "default"),
            "admin_password_configured": bool(_get_admin_password_hash()),
            "legacy_access_password_enabled": bool(ACCESS_PASSWORD),
            "unified_password_configured": bool(_get_unified_password()),
            "unified_password_source": "runtime" if _decrypt_setting(local_settings.get("unified_password")) else ("env" if UNIFIED_PASSWORD else "none"),
            "resend_api_key_configured": bool(resend_runtime or RESEND_API_KEY),
            "resend_api_key_source": "runtime" if resend_runtime else ("env" if RESEND_API_KEY else "none"),
            "gmail": imap_settings,
            "microsoft": imap_settings.get("microsoft", {}) if isinstance(imap_settings, dict) else {},
            "gmail_error": imap_error,
        },
    })


@app.route("/api/settings", methods=["POST"])
@login_required
def save_runtime_settings():
    """保存后台运行配置。空白敏感字段表示保留原值。"""
    confirm = _require_sensitive_confirmation("save_settings")
    if confirm:
        return confirm
    data = request.json or {}
    admin_username = (data.get("admin_username") or "").strip()
    if not admin_username:
        return jsonify({"success": False, "message": "管理员账号不能为空"}), 400
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,64}", admin_username):
        return jsonify({"success": False, "message": "管理员账号需为 3-64 位，可包含字母、数字、点、下划线、@ 或 -"}), 400
    if data.get("admin_password") and len(data.get("admin_password", "")) < 10:
        return jsonify({"success": False, "message": "后台登录密码至少需要 10 位"}), 400
    try:
        local_settings = _update_viewer_runtime_settings(data)
    except Exception as e:
        app.logger.error(f"保存后台配置失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "保存后台配置失败"}), 500

    imap_payload = {
        "public_base_url": data.get("public_base_url", ""),
        "google_client_id": data.get("google_client_id", ""),
        "clear_google_client_secret": bool(data.get("clear_google_client_secret")),
        "microsoft_client_id": data.get("microsoft_client_id", ""),
        "clear_microsoft_client_secret": bool(data.get("clear_microsoft_client_secret")),
    }
    if data.get("google_client_secret"):
        imap_payload["google_client_secret"] = data.get("google_client_secret", "")
    if data.get("microsoft_client_secret"):
        imap_payload["microsoft_client_secret"] = data.get("microsoft_client_secret", "")

    imap_settings, imap_error = _save_imap_runtime_settings(imap_payload)
    if imap_error:
        return jsonify({"success": False, "message": f"本地配置已保存，但 Gmail OAuth 配置保存失败: {imap_error}"}), 502

    resend_runtime = _decrypt_setting(local_settings.get("resend_api_key"))
    unified_runtime = _decrypt_setting(local_settings.get("unified_password"))
    _append_audit(local_settings, "settings.updated", {
        "admin_password": bool(data.get("admin_password")),
        "unified_password": bool(data.get("unified_password") or data.get("clear_unified_password")),
        "resend": bool(data.get("resend_api_key") or data.get("clear_resend_api_key")),
        "oauth": bool(imap_payload),
    })
    session["username"] = (local_settings.get("admin_username") or _get_admin_username()).strip()
    _update_session_seen(local_settings)
    _write_viewer_settings(local_settings)
    return jsonify({
        "success": True,
        "settings": {
            "admin_username": _get_admin_username(),
            "admin_username_source": "runtime" if (local_settings.get("admin_username") or "").strip() else ("env" if ADMIN_USERNAME else "default"),
            "admin_password_configured": bool(_get_admin_password_hash()),
            "legacy_access_password_enabled": bool(ACCESS_PASSWORD),
            "unified_password_configured": bool(unified_runtime or UNIFIED_PASSWORD),
            "unified_password_source": "runtime" if unified_runtime else ("env" if UNIFIED_PASSWORD else "none"),
            "resend_api_key_configured": bool(resend_runtime or RESEND_API_KEY),
            "resend_api_key_source": "runtime" if resend_runtime else ("env" if RESEND_API_KEY else "none"),
            "gmail": imap_settings,
            "microsoft": imap_settings.get("microsoft", {}) if isinstance(imap_settings, dict) else {},
        },
    })


@app.route("/api/ai/settings", methods=["GET"])
@login_required
def get_ai_settings():
    ai = _get_ai_settings()
    return jsonify({
        "success": True,
        "channels": [_safe_ai_channel(channel) for channel in ai.get("channels", [])],
        "default_model": ai.get("default_model", {}),
    })


@app.route("/api/ai/channels", methods=["POST"])
@login_required
def add_ai_channel():
    data = request.json or {}
    provider = _normalize_ai_provider(data.get("provider", ""))
    api_key = (data.get("api_key") or "").strip()
    if not provider:
        return jsonify({"success": False, "message": "请选择有效的 AI 渠道类型"}), 400
    if not api_key:
        return jsonify({"success": False, "message": "请填写 API Key"}), 400
    base_url = _normalize_ai_base_url(provider, data.get("base_url", ""))
    if provider == "openai_compatible" and not base_url:
        return jsonify({"success": False, "message": "第三方中转站需要填写 Base URL"}), 400
    name = (data.get("name") or "").strip()[:80] or {
        "openai": "OpenAI",
        "gemini": "Gemini",
        "openai_compatible": "OpenAI Compatible",
    }[provider]

    try:
        models = _fetch_ai_models(provider, base_url, api_key)
    except Exception as e:
        app.logger.warning(f"AI 模型拉取失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 502
    if not models:
        return jsonify({"success": False, "message": "未拉取到可用模型，请检查 Key 或 Base URL"}), 502

    ai = _get_ai_settings()
    now = datetime.now(timezone.utc).isoformat()
    channel = {
        "id": uuid.uuid4().hex,
        "name": name,
        "provider": provider,
        "base_url": base_url,
        "api_key": _encrypt_setting(api_key),
        "models": models,
        "created_at": now,
        "updated_at": now,
    }
    ai["channels"].append(channel)
    if not ai.get("default_model"):
        ai["default_model"] = {"channel_id": channel["id"], "model": models[0]}
    _write_ai_settings(ai)
    return jsonify({
        "success": True,
        "channel": _safe_ai_channel(channel),
        "channels": [_safe_ai_channel(item) for item in ai.get("channels", [])],
        "default_model": ai.get("default_model", {}),
    })


@app.route("/api/ai/channels/<channel_id>/models", methods=["POST"])
@login_required
def refresh_ai_channel_models(channel_id):
    ai = _get_ai_settings()
    channel = next((item for item in ai.get("channels", []) if item.get("id") == channel_id), None)
    if not channel:
        return jsonify({"success": False, "message": "渠道不存在"}), 404
    api_key = _decrypt_setting(channel.get("api_key"))
    if not api_key:
        return jsonify({"success": False, "message": "渠道 API Key 不可用，请重新新增渠道"}), 400
    try:
        models = _fetch_ai_models(channel.get("provider", ""), channel.get("base_url", ""), api_key)
    except Exception as e:
        app.logger.warning(f"AI 模型刷新失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 502
    if not models:
        return jsonify({"success": False, "message": "未拉取到可用模型"}), 502
    channel["models"] = models
    channel["updated_at"] = datetime.now(timezone.utc).isoformat()
    default_model = ai.get("default_model", {})
    if default_model.get("channel_id") == channel_id and default_model.get("model") not in models:
        ai["default_model"] = {"channel_id": channel_id, "model": models[0]}
    _write_ai_settings(ai)
    return jsonify({
        "success": True,
        "channel": _safe_ai_channel(channel),
        "channels": [_safe_ai_channel(item) for item in ai.get("channels", [])],
        "default_model": ai.get("default_model", {}),
    })


@app.route("/api/ai/channels/<channel_id>", methods=["DELETE"])
@login_required
def delete_ai_channel(channel_id):
    ai = _get_ai_settings()
    channels = ai.get("channels", [])
    ai["channels"] = [item for item in channels if item.get("id") != channel_id]
    default_model = ai.get("default_model", {})
    if default_model.get("channel_id") == channel_id:
        ai["default_model"] = {}
    _write_ai_settings(ai)
    return jsonify({
        "success": True,
        "channels": [_safe_ai_channel(item) for item in ai.get("channels", [])],
        "default_model": ai.get("default_model", {}),
    })


@app.route("/api/ai/default-model", methods=["POST"])
@login_required
def save_ai_default_model():
    data = request.json or {}
    channel_id = (data.get("channel_id") or "").strip()
    model = (data.get("model") or "").strip()
    ai = _get_ai_settings()
    channel = next((item for item in ai.get("channels", []) if item.get("id") == channel_id), None)
    if not channel:
        return jsonify({"success": False, "message": "请选择有效渠道"}), 400
    models = channel.get("models", []) if isinstance(channel.get("models"), list) else []
    if model not in models:
        return jsonify({"success": False, "message": "请选择该渠道下已保存的模型"}), 400
    ai["default_model"] = {"channel_id": channel_id, "model": model}
    _write_ai_settings(ai)
    return jsonify({"success": True, "default_model": ai["default_model"]})


@app.route("/api/ai/translate", methods=["POST"])
@login_required
def translate_mail_to_chinese():
    started_at = time.time()
    try:
        data = request.get_json(silent=True) or {}
        html_content = (data.get("html", "") or "").strip()
        text_content = (data.get("text", "") or "").strip()
        subject_raw = (data.get("subject", "") or "").strip()
        html_detected = bool(html_content)
        html_length = len(html_content)
        text_length = len(text_content)
        content, wants_html, original_length, truncated = _prepare_translation_payload(html_content, data.get("text", ""))
        subject = _extract_plain_text(subject_raw)
        app.logger.info(
            "邮件翻译开始: subject=%s html_detected=%s html_chars=%s text_chars=%s wants_html=%s payload_chars=%s original_chars=%s truncated=%s preview=%s",
            subject[:120],
            html_detected,
            html_length,
            text_length,
            wants_html,
            len(content),
            original_length,
            truncated,
            _translation_preview(content),
        )
        if subject:
            content = f"主题：{subject}\n\n{content}"
            original_length += len(subject)
            if len(content) > AI_TRANSLATION_MAX_CHARS:
                content = content[:AI_TRANSLATION_MAX_CHARS]
                truncated = True
            app.logger.info(
                "邮件翻译主题已拼接: subject_chars=%s payload_chars=%s preview=%s",
                len(subject),
                len(content),
                _translation_preview(content),
            )
        if not content:
            return jsonify({"success": False, "message": "没有可翻译的邮件内容"}), 400

        ai = _get_ai_settings()
        default_model = ai.get("default_model", {})
        channel = next((item for item in ai.get("channels", []) if item.get("id") == default_model.get("channel_id")), None)
        model = default_model.get("model", "")
        if not channel or not model:
            return jsonify({"success": False, "message": "请先在 AI 设置中配置默认模型"}), 400
        api_key = _decrypt_setting(channel.get("api_key"))
        if not api_key:
            return jsonify({"success": False, "message": "默认渠道 API Key 不可用，请重新新增渠道"}), 400

        app.logger.info(
            "邮件翻译准备请求: provider=%s model=%s base_url=%s wants_html=%s payload_chars=%s",
            channel.get("provider"),
            model,
            channel.get("base_url", ""),
            wants_html,
            len(content),
        )

        if channel.get("provider") == "gemini":
            translated = _call_gemini(channel, api_key, model, content, wants_html)
        else:
            translated = _call_openai_chat(channel, api_key, model, content, wants_html)
    except (requests.Timeout, requests.ConnectionError) as e:
        app.logger.warning(
            "邮件翻译网络超时/连接失败: provider=%s model=%s chars=%s wants_html=%s timeout=%s error=%s",
            channel.get("provider") if "channel" in locals() and isinstance(channel, dict) else "",
            model if "model" in locals() else "",
            len(content) if "content" in locals() else 0,
            wants_html if "wants_html" in locals() else False,
            AI_TRANSLATION_TIMEOUT_SECONDS,
            e,
        )
        return jsonify({
            "success": False,
            "message": f"翻译请求超时（{AI_TRANSLATION_TIMEOUT_SECONDS} 秒），请稍后重试或换一个更快的默认模型",
        }), 504
    except Exception as e:
        elapsed_ms = int((time.time() - started_at) * 1000)
        app.logger.warning("邮件翻译失败: %s elapsed_ms=%s", e, elapsed_ms, exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e) or "翻译服务异常，请查看 mail-viewer 日志",
            "elapsed_ms": elapsed_ms,
        }), 502
    if not translated:
        return jsonify({"success": False, "message": "模型没有返回翻译内容"}), 502
    app.logger.info(
        "邮件翻译原始返回: provider=%s model=%s output_chars=%s preview=%s",
        channel.get("provider"),
        model,
        len(translated),
        _translation_preview(translated),
    )
    translated = _normalize_ai_translation(translated, wants_html)
    elapsed_ms = int((time.time() - started_at) * 1000)
    app.logger.info(
        "邮件翻译完成: provider=%s model=%s format=%s input_chars=%s original_chars=%s output_chars=%s truncated=%s elapsed_ms=%s preview=%s",
        channel.get("provider"),
        model,
        "html" if wants_html else "text",
        len(content),
        original_length,
        len(translated),
        truncated,
        elapsed_ms,
        _translation_preview(translated),
    )
    return jsonify({
        "success": True,
        "translation": translated,
        "format": "html" if wants_html else "text",
        "elapsed_ms": elapsed_ms,
        "input_chars": len(content),
        "original_chars": original_length,
        "truncated": truncated,
    })


@app.route("/api/mailboxes", methods=["GET"])
@login_required
def list_mailboxes():
    settings = _read_viewer_settings()
    mailboxes = settings.get("mailboxes", [])
    if not isinstance(mailboxes, list):
        mailboxes = []
    normalized = []
    for item in mailboxes:
        if not isinstance(item, dict):
            continue
        address = _normalize_mailbox_address(item.get("address", ""))
        if not address:
            continue
        normalized.append({
            **item,
            "address": address,
            "display_name": _normalize_display_name(item.get("display_name", ""), address),
            "send_name": _normalize_display_name(item.get("send_name", ""), ""),
            "group": _normalize_account_group(item.get("group", "")),
        })
    mailboxes = normalized
    return jsonify({
        "success": True,
        "mailboxes": mailboxes,
        "account_order": _normalize_account_order(settings.get("account_order", [])),
    })


@app.route("/api/mailboxes", methods=["POST"])
@login_required
def add_mailbox():
    data = request.json or {}
    address = _normalize_mailbox_address(data.get("address", ""))
    display_name = _normalize_display_name(data.get("display_name", ""), address)
    send_name = _normalize_display_name(data.get("send_name", ""), "")
    group = _normalize_account_group(data.get("group", ""))
    if not address:
        return jsonify({"success": False, "message": "邮箱地址格式不正确"}), 400

    settings = _read_viewer_settings()
    mailboxes = settings.get("mailboxes", [])
    if not isinstance(mailboxes, list):
        mailboxes = []
    now = datetime.now(timezone.utc).isoformat()
    existing = next((item for item in mailboxes if item.get("address") == address), None)
    if existing:
        existing["display_name"] = display_name
        existing["send_name"] = send_name
        existing["group"] = group
        existing["updated_at"] = now
    else:
        mailboxes.append({
            "address": address,
            "display_name": display_name,
            "send_name": send_name,
            "group": group,
            "created_at": now,
            "updated_at": now,
        })
    settings["mailboxes"] = mailboxes
    order = _normalize_account_order(settings.get("account_order", []))
    key = f"local:{address}"
    if key not in order:
        order.append(key)
    settings["account_order"] = order
    _touch_sync_event(settings, "mailbox.upserted", {"address": address})
    _write_viewer_settings(settings)
    return jsonify({
        "success": True,
        "mailbox": {"address": address, "display_name": display_name, "send_name": send_name, "group": group},
        "mailboxes": mailboxes,
        "account_order": order,
    })


@app.route("/api/mailboxes/<path:address>", methods=["PATCH"])
@login_required
def update_mailbox(address):
    normalized = _normalize_mailbox_address(address)
    if not normalized:
        return jsonify({"success": False, "message": "邮箱地址格式不正确"}), 400
    data = request.json or {}
    settings = _read_viewer_settings()
    mailboxes = settings.get("mailboxes", [])
    if not isinstance(mailboxes, list):
        mailboxes = []
    existing = next((item for item in mailboxes if item.get("address") == normalized), None)
    if not existing:
        return jsonify({"success": False, "message": "邮箱不存在"}), 404
    existing["display_name"] = _normalize_display_name(data.get("display_name", ""), normalized)
    existing["send_name"] = _normalize_display_name(data.get("send_name", ""), "")
    existing["group"] = _normalize_account_group(data.get("group", ""))
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    settings["mailboxes"] = mailboxes
    _touch_sync_event(settings, "mailbox.upserted", {"address": normalized})
    _write_viewer_settings(settings)
    return jsonify({
        "success": True,
        "mailbox": existing,
        "mailboxes": mailboxes,
        "account_order": _normalize_account_order(settings.get("account_order", [])),
    })


@app.route("/api/mailboxes/<path:address>", methods=["DELETE"])
@login_required
def delete_mailbox(address):
    confirm = _require_sensitive_confirmation("delete_mailbox")
    if confirm:
        return confirm
    normalized = _normalize_mailbox_address(address)
    settings = _read_viewer_settings()
    mailboxes = settings.get("mailboxes", [])
    if not isinstance(mailboxes, list):
        mailboxes = []
    settings["mailboxes"] = [item for item in mailboxes if item.get("address") != normalized]
    settings["account_order"] = [
        item for item in _normalize_account_order(settings.get("account_order", []))
        if item != f"local:{normalized}"
    ]
    _append_audit(settings, "mailbox.deleted", {"address": normalized})
    _touch_sync_event(settings, "mailbox.deleted", {"address": normalized})
    _write_viewer_settings(settings)
    return jsonify({
        "success": True,
        "mailboxes": settings["mailboxes"],
        "account_order": settings["account_order"],
    })


@app.route("/api/mailboxes/reorder", methods=["POST"])
@login_required
def reorder_mailboxes():
    data = request.json or {}
    order = _normalize_account_order(data.get("order", []))
    if not order:
        return jsonify({"success": False, "message": "排序参数不正确"}), 400
    settings = _read_viewer_settings()
    mailboxes = settings.get("mailboxes", [])
    if not isinstance(mailboxes, list):
        mailboxes = []
    by_address = {
        _normalize_mailbox_address(item.get("address", "")): item
        for item in mailboxes
        if isinstance(item, dict) and _normalize_mailbox_address(item.get("address", ""))
    }
    local_order = [
        item.removeprefix("local:")
        for item in order
        if item.startswith("local:")
    ] + [
        item.removeprefix("localEmail:")
        for item in order
        if item.startswith("localEmail:")
    ]
    local_order = list(dict.fromkeys(local_order))
    reordered = [by_address[address] for address in local_order if address in by_address]
    reordered.extend(item for address, item in by_address.items() if address not in local_order)
    settings["mailboxes"] = reordered
    settings["account_order"] = order
    _touch_sync_event(settings, "mailbox.reordered", {"order": order})
    _write_viewer_settings(settings)
    return jsonify({"success": True, "mailboxes": reordered, "account_order": order})


@app.route("/api/inbox/detail", methods=["POST"])
@login_required
def inbox_detail():
    """通用收件箱邮件详情"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    message_id = data.get("message_id", "").strip()

    if not email or not message_id:
        return jsonify({"success": False, "message": "缺少必要参数"})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")

    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0]})

        detail_resp = http_session.get(
            f"{base_url}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30
        )

        if detail_resp.status_code != 200:
            return jsonify({"success": False, "message": "获取邮件详情失败"})

        detail = detail_resp.json()
        if isinstance(detail, dict):
            detail["html"] = _prepare_html_for_render(detail.get("html", ""))
            detail["attachments"] = _format_attachments(detail)
            settings = _read_viewer_settings()
            detail["meta"] = _public_message_meta(_get_message_meta(settings, "local", email, "inbox", message_id))
        return jsonify({"success": True, "detail": detail})

    except Exception as e:
        app.logger.error(f"获取邮件详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


@app.route("/api/inbox/attachment/<message_id>/<attachment_id>")
@login_required
def inbox_attachment(message_id, attachment_id):
    """代理下载本地收件附件。"""
    email = request.args.get("email", "").strip()
    password = request.args.get("password", "").strip() or _get_unified_password()
    if not email:
        return jsonify({"success": False, "message": "缺少邮箱参数"}), 400

    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    token, err = _get_mail_token(email, password)
    if err:
        return jsonify({"success": False, "message": err[0]}), err[1]

    headers = {"Authorization": f"Bearer {token}"}
    url, download_resp = _find_attachment_download_url(base_url, message_id, attachment_id, headers)
    if not download_resp:
        return jsonify({"success": False, "message": "附件下载接口不可用"}), 404

    try:
        filename = request.args.get("filename", "attachment")
        content_type = download_resp.headers.get("Content-Type", "application/octet-stream")
        proxied = Response(stream_with_context(download_resp.iter_content(65536)), content_type=content_type)
        proxied.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        if "Content-Length" in download_resp.headers:
            proxied.headers["Content-Length"] = download_resp.headers["Content-Length"]
        return proxied
    except Exception as e:
        app.logger.error(f"附件下载失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "附件下载失败"}), 502


# ---- 批量操作 API ----

@app.route("/api/inbox/batch", methods=["POST"])
@login_required
def inbox_batch():
    """批量操作邮件"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    action = data.get("action", "").strip()
    message_ids = data.get("message_ids", [])

    if not email or not action or not message_ids:
        return jsonify({"success": False, "message": "缺少必要参数"})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")

    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0]})

        batch_resp = http_session.post(
            f"{base_url}/messages/batch",
            json={"action": action, "message_ids": message_ids},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if batch_resp.status_code == 200:
            return jsonify({"success": True, **batch_resp.json()})
        else:
            detail = "操作失败"
            try:
                detail = batch_resp.json().get("detail", detail)
            except Exception:
                pass
            return jsonify({"success": False, "message": detail})

    except Exception as e:
        app.logger.error(f"批量操作失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


@app.route("/api/inbox/mark", methods=["POST"])
@login_required
def inbox_mark():
    """单封本地邮件标记已读 / 未读。"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    message_id = data.get("message_id", "").strip()
    seen = bool(data.get("seen"))

    if not email or not message_id:
        return jsonify({"success": False, "message": "缺少必要参数"})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0]})
        action = "mark_read" if seen else "mark_unread"
        batch_resp = http_session.post(
            f"{base_url}/messages/batch",
            json={"action": action, "message_ids": [message_id]},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if batch_resp.status_code == 200:
            return jsonify({"success": True, **batch_resp.json()})
        return jsonify({"success": False, "message": _extract_api_error(batch_resp, "标记失败")})
    except Exception as e:
        app.logger.error(f"标记邮件失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


@app.route("/api/message-meta", methods=["GET"])
@login_required
def get_message_meta():
    account_type = request.args.get("account_type", "local")
    account_id = request.args.get("account_id", "")
    folder = request.args.get("folder", "inbox")
    message_id = request.args.get("message_id", "")
    settings = _read_viewer_settings()
    return jsonify({
        "success": True,
        "meta": _public_message_meta(_get_message_meta(settings, account_type, account_id, folder, message_id)),
    })


@app.route("/api/message-meta/batch", methods=["POST"])
@login_required
def get_message_meta_batch():
    data = request.json or {}
    refs = data.get("refs", [])
    if not isinstance(refs, list):
        return jsonify({"success": False, "message": "refs 必须是数组"}), 400
    settings = _read_viewer_settings()
    result = {}
    for ref in refs[:500]:
        if not isinstance(ref, dict):
            continue
        account_type = ref.get("account_type", "local")
        account_id = ref.get("account_id", "")
        folder = ref.get("folder", "inbox")
        message_id = str(ref.get("message_id", "")).strip()
        if not account_id or not message_id:
            continue
        key = _message_meta_key(account_type, account_id, folder, message_id)
        result[key] = _public_message_meta(_get_message_meta(settings, account_type, account_id, folder, message_id))
    return jsonify({"success": True, "meta": result})


@app.route("/api/message-meta", methods=["POST"])
@login_required
def save_message_meta():
    data = request.json or {}
    account_type = data.get("account_type", "local")
    account_id = data.get("account_id", "")
    folder = data.get("folder", "inbox")
    message_id = str(data.get("message_id", "")).strip()
    if not account_id or not message_id:
        return jsonify({"success": False, "message": "缺少必要参数"}), 400
    settings = _read_viewer_settings()
    item = _merge_message_meta(settings, account_type, account_id, folder, message_id, data.get("meta") or {})
    _touch_sync_event(settings, "message.meta.updated", {
        "account_type": account_type,
        "account_id": account_id,
        "folder": folder,
        "message_id": message_id,
    })
    _write_viewer_settings(settings)
    return jsonify({"success": True, "meta": _public_message_meta(item)})


@app.route("/api/keyword-rules", methods=["GET"])
@login_required
def list_keyword_rules():
    settings = _read_viewer_settings()
    rules = [
        _public_keyword_rule(item)
        for item in _settings_list(settings, "keyword_rules")
        if _normalize_keyword_rule(item)
    ]
    return jsonify({"success": True, "rules": rules})


@app.route("/api/keyword-rules", methods=["POST"])
@login_required
def save_keyword_rule():
    data = request.json or {}
    settings = _read_viewer_settings()
    rules = [
        _normalize_keyword_rule(item)
        for item in _settings_list(settings, "keyword_rules")
    ]
    rules = [item for item in rules if item]
    now = _iso_now()
    incoming = _normalize_keyword_rule({
        **data,
        "updated_at": now,
        "created_at": data.get("created_at") or now,
    })
    if not incoming:
        return jsonify({"success": False, "message": "规则名称和关键词不能为空"}), 400
    existing = next((item for item in rules if item.get("id") == incoming["id"]), None)
    if existing:
        incoming["created_at"] = existing.get("created_at", incoming["created_at"])
        existing.update(incoming)
    else:
        rules.append(incoming)
    settings["keyword_rules"] = rules[-200:]
    _touch_sync_event(settings, "keyword_rule.upserted", {"id": incoming["id"]})
    _write_viewer_settings(settings)
    return jsonify({"success": True, "rule": _public_keyword_rule(incoming), "rules": [_public_keyword_rule(item) for item in rules]})


@app.route("/api/keyword-rules/<rule_id>", methods=["DELETE"])
@login_required
def delete_keyword_rule(rule_id):
    settings = _read_viewer_settings()
    rules = [
        _normalize_keyword_rule(item)
        for item in _settings_list(settings, "keyword_rules")
    ]
    rules = [item for item in rules if item and item.get("id") != rule_id]
    settings["keyword_rules"] = rules
    _touch_sync_event(settings, "keyword_rule.deleted", {"id": rule_id})
    _write_viewer_settings(settings)
    return jsonify({"success": True, "rules": [_public_keyword_rule(item) for item in rules]})


# ---- 搜索邮件 API ----

@app.route("/api/inbox/search", methods=["POST"])
@login_required
def inbox_search():
    """搜索邮件"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    query = data.get("query", "").strip()

    if not email or not query:
        return jsonify({"success": False, "message": "缺少必要参数", "messages": []})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")

    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0], "messages": []})

        search_resp = http_session.get(
            f"{base_url}/messages/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if search_resp.status_code != 200:
            return jsonify({"success": False, "message": "搜索失败", "messages": []})

        messages = search_resp.json()
        if isinstance(messages, dict):
            messages = messages.get("hydra:member", [])

        for msg in messages:
            subject = msg.get("subject", "")
            intro = msg.get("intro", "")
            text = f"{subject} {intro}"
            code_match = re.search(r"\b(\d{6})\b", text)
            msg["extracted_code"] = code_match.group(1) if code_match else None

        return jsonify({"success": True, "messages": messages})

    except Exception as e:
        app.logger.error(f"搜索邮件失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试", "messages": []})


# ---- 删除邮件 API ----

@app.route("/api/inbox/delete", methods=["POST"])
@login_required
def inbox_delete():
    """删除邮件（软删除）"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    message_id = data.get("message_id", "").strip()

    if not email or not message_id:
        return jsonify({"success": False, "message": "缺少必要参数"})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")

    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0]})

        del_resp = http_session.delete(
            f"{base_url}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

        if del_resp.status_code == 200:
            return jsonify({"success": True, "message": "邮件已删除"})
        else:
            return jsonify({"success": False, "message": f"删除失败 (HTTP {del_resp.status_code})"})

    except Exception as e:
        app.logger.error(f"删除邮件失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


# ---- Trash / 恢复 / 彻底删除 API ----

@app.route("/api/trash/query", methods=["POST"])
@login_required
def trash_query():
    """查询回收站邮件。"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    offset = int(data.get("offset", 0))
    limit = int(data.get("limit", 30))

    if not email:
        return jsonify({"success": False, "message": "缺少邮箱地址", "messages": []})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0], "messages": []})

        headers = {"Authorization": f"Bearer {token}"}
        trash_resp = http_session.get(
            f"{base_url}/messages/trash",
            params={"offset": offset, "limit": limit},
            headers=headers,
            timeout=30,
        )
        if trash_resp.status_code == 404:
            trash_resp = http_session.get(
                f"{base_url}/trash",
                params={"offset": offset, "limit": limit},
                headers=headers,
                timeout=30,
            )
        if trash_resp.status_code != 200:
            return jsonify({"success": False, "message": "回收站接口不可用", "messages": []})

        resp_data = trash_resp.json()
        messages = resp_data.get("hydra:member", []) if isinstance(resp_data, dict) else resp_data
        total = resp_data.get("hydra:totalItems", len(messages)) if isinstance(resp_data, dict) else len(messages)
        settings = _read_viewer_settings()
        _attach_message_meta(settings, messages, "local", email, "trash")
        return jsonify({"success": True, "messages": messages, "total": total, "offset": offset, "limit": limit})

    except Exception as e:
        app.logger.error(f"查询回收站失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试", "messages": []})


@app.route("/api/inbox/restore", methods=["POST"])
@login_required
def inbox_restore():
    """从回收站恢复邮件。"""
    return _message_action(["restore"], "邮件已恢复")


@app.route("/api/inbox/permanent-delete", methods=["POST"])
@login_required
def inbox_permanent_delete():
    """彻底删除邮件。"""
    return _message_action(["permanent-delete", "permanent_delete", "purge"], "邮件已彻底删除")


def _message_action(actions: list[str], ok_message: str):
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    message_id = data.get("message_id", "").strip()

    if not email or not message_id:
        return jsonify({"success": False, "message": "缺少必要参数"})
    if any(action in {"permanent-delete", "permanent_delete", "purge"} for action in actions):
        confirm = _require_sensitive_confirmation("permanent_delete")
        if confirm:
            return confirm

    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0]})

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        last_resp = None
        for action in actions:
            endpoints = []
            if action in {"permanent-delete", "permanent_delete", "purge"}:
                endpoints.append(("delete", f"{base_url}/messages/{message_id}/permanent", None))
            endpoints.extend([
                ("post", f"{base_url}/messages/{message_id}/{action}", None),
                ("patch", f"{base_url}/messages/{message_id}", {"action": action}),
                ("post", f"{base_url}/messages/batch", {"action": action, "message_ids": [message_id]}),
            ])
            for method, url, payload in endpoints:
                resp = http_session.request(method, url, json=payload, headers=headers, timeout=30)
                last_resp = resp
                if resp.status_code in (200, 204):
                    payload = resp.json() if resp.content else {}
                    return jsonify({"success": True, "message": ok_message, **payload})
                if resp.status_code not in (404, 405, 422):
                    detail = _extract_api_error(resp, ok_message)
                    return jsonify({"success": False, "message": detail}), resp.status_code

        detail = _extract_api_error(last_resp, "后端暂未提供该操作接口") if last_resp else "后端暂未提供该操作接口"
        return jsonify({"success": False, "message": detail})

    except Exception as e:
        app.logger.error(f"邮件操作失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


# ---- 已发送邮件查询 API ----

@app.route("/api/sent/detail", methods=["POST"])
@login_required
def sent_detail():
    """查询已发送邮件详情。"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    message_id = data.get("message_id", "").strip()

    if not email or not message_id:
        return jsonify({"success": False, "message": "缺少必要参数"})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")
    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0]})

        detail_resp = http_session.get(
            f"{base_url}/sent/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if detail_resp.status_code != 200:
            detail = _extract_api_error(detail_resp, "获取已发送详情失败")
            return jsonify({"success": False, "message": detail})

        detail = detail_resp.json()
        if isinstance(detail, dict):
            detail["html"] = _prepare_html_for_render(detail.get("html", ""))
            settings = _read_viewer_settings()
            detail["meta"] = _public_message_meta(_get_message_meta(settings, "local", email, "sent", message_id))
        return jsonify({"success": True, "detail": detail})

    except Exception as e:
        app.logger.error(f"获取已发送详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


@app.route("/api/sent/query", methods=["POST"])
@login_required
def sent_query():
    """查询已发送邮件"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()

    if not email:
        return jsonify({"success": False, "message": "缺少邮箱地址", "messages": []})

    base_url = DUCKMAIL_BASE_URL.rstrip("/")

    try:
        token, err = _get_mail_token(email, password)
        if err:
            return jsonify({"success": False, "message": err[0], "messages": []})

        offset = int(data.get("offset", 0))
        limit = int(data.get("limit", 30))
        sent_resp = http_session.get(
            f"{base_url}/sent",
            params={"offset": offset, "limit": limit},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if sent_resp.status_code != 200:
            return jsonify({"success": False, "message": "查询已发送失败", "messages": []})

        resp_data = sent_resp.json()
        if isinstance(resp_data, dict):
            messages = resp_data.get("hydra:member", [])
            total = resp_data.get("hydra:totalItems", len(messages))
        else:
            messages = resp_data
            total = len(messages)
        if total == len(messages) and len(messages) > limit:
            messages = messages[offset:offset + limit]
        for msg in messages:
            if isinstance(msg, dict):
                msg["html"] = _prepare_html_for_render(msg.get("html", ""))
        settings = _read_viewer_settings()
        _attach_message_meta(settings, messages, "local", email, "sent")

        return jsonify({"success": True, "messages": messages, "total": total, "offset": offset, "limit": limit})

    except Exception as e:
        app.logger.error(f"查询已发送邮件失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试", "messages": []})


# ---- 草稿 / 发送失败重试 API ----

@app.route("/api/drafts", methods=["GET"])
@login_required
def list_drafts():
    account_id = request.args.get("account_id", "").strip()
    account_type = request.args.get("account_type", "").strip()
    settings = _read_viewer_settings()
    drafts = [
        _public_draft(item)
        for item in _settings_list(settings, "drafts")
        if (not account_id or item.get("account_id") == account_id or item.get("from_email") == account_id)
        and (not account_type or item.get("account_type") == account_type)
    ]
    drafts.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return jsonify({"success": True, "drafts": drafts})


@app.route("/api/drafts", methods=["POST"])
@login_required
def save_draft():
    data = request.json or {}
    settings = _read_viewer_settings()
    drafts = _settings_list(settings, "drafts")
    draft_id = str(data.get("id") or "").strip()
    now = _iso_now()
    existing = next((item for item in drafts if item.get("id") == draft_id), None) if draft_id else None
    if not existing:
        existing = {"id": _new_id("drf_"), "created_at": now, "version": 0}
        drafts.append(existing)
    existing.update({
        "account_type": data.get("account_type", "local"),
        "account_id": data.get("account_id") or data.get("from_email") or "",
        "from_email": data.get("from_email", ""),
        "from_name": data.get("from_name", ""),
        "to": data.get("to", ""),
        "subject": data.get("subject", ""),
        "text": data.get("text", ""),
        "html": data.get("html", ""),
        "updated_at": now,
        "version": _safe_int(existing.get("version"), 0) + 1,
    })
    settings["drafts"] = drafts[-500:]
    _touch_sync_event(settings, "draft.upserted", {"id": existing["id"]})
    _write_viewer_settings(settings)
    return jsonify({"success": True, "draft": _public_draft(existing)})


@app.route("/api/drafts/<draft_id>", methods=["DELETE"])
@login_required
def delete_draft(draft_id):
    settings = _read_viewer_settings()
    drafts = _settings_list(settings, "drafts")
    settings["drafts"] = [item for item in drafts if item.get("id") != draft_id]
    _touch_sync_event(settings, "draft.deleted", {"id": draft_id})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


@app.route("/api/outbox", methods=["POST"])
@login_required
def create_outbox_failure():
    data = request.json or {}
    settings = _read_viewer_settings()
    item = _store_outbox_failure(settings, data, data.get("error") or "发送失败", data.get("account_type") or "external")
    _append_audit(settings, "mail.send.failed", {
        "from": data.get("from_email", ""),
        "to": data.get("to", ""),
        "reason": item.get("error", ""),
        "account_type": item.get("account_type", ""),
    }, success=False)
    _write_viewer_settings(settings)
    return jsonify({"success": True, "message": _public_outbox(item)})


@app.route("/api/outbox", methods=["GET"])
@login_required
def list_outbox():
    account_id = request.args.get("account_id", "").strip()
    account_type = request.args.get("account_type", "").strip()
    include_sent = request.args.get("include_sent") == "1"
    settings = _read_viewer_settings()
    outbox = [
        _public_outbox(item)
        for item in _settings_list(settings, "outbox")
        if (not account_id or item.get("account_id") == account_id or item.get("from_email") == account_id)
        and (not account_type or item.get("account_type") == account_type)
        and (include_sent or item.get("status") != "sent")
    ]
    outbox.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return jsonify({"success": True, "messages": outbox})


@app.route("/api/outbox/<message_id>/retry", methods=["POST"])
@login_required
def retry_outbox(message_id):
    settings = _read_viewer_settings()
    outbox = _settings_list(settings, "outbox")
    item = next((entry for entry in outbox if entry.get("id") == message_id), None)
    if not item:
        return jsonify({"success": False, "message": "重试记录不存在"}), 404
    item["attempts"] = _safe_int(item.get("attempts"), 0) + 1
    item["last_attempt_at"] = _iso_now()
    item["updated_at"] = item["last_attempt_at"]
    _write_viewer_settings(settings)
    if item.get("account_type") == "external":
        account_id = str(item.get("account_id") or "").strip()
        if not account_id:
            item["status"] = "failed"
            item["error"] = "缺少外部账号 ID"
            _touch_sync_event(settings, "outbox.failed", {"id": item.get("id")})
            _write_viewer_settings(settings)
            return jsonify({"success": False, "message": item["error"]}), 400
        try:
            target = urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", f"api/accounts/{quote(account_id, safe='')}/send")
            resp = http_session.post(
                target,
                json={
                    "to": item.get("to", ""),
                    "subject": item.get("subject", ""),
                    "text": item.get("text", ""),
                    "html": item.get("html", ""),
                    "fromName": item.get("from_name", ""),
                },
                timeout=45,
            )
            if resp.status_code == 200:
                _update_outbox_item(settings, message_id, "sent")
                _append_audit(settings, "mail.retry.success", {
                    "id": message_id,
                    "account_type": "external",
                    "to": item.get("to", ""),
                })
                _touch_sync_event(settings, "outbox.sent", {"id": message_id})
                _write_viewer_settings(settings)
                return jsonify({"success": True, "message": "重试发送成功"})
            error_msg = _response_error(resp, f"重试发送失败 (HTTP {resp.status_code})")
        except Exception as e:
            app.logger.error(f"外部账号重试发送失败: {e}", exc_info=True)
            error_msg = "外部账号重试发送失败，请稍后再试"
        _update_outbox_item(settings, message_id, "failed", error_msg)
        _append_audit(settings, "mail.retry.failed", {
            "id": message_id,
            "account_type": "external",
            "reason": error_msg,
        }, success=False)
        _touch_sync_event(settings, "outbox.failed", {"id": message_id})
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": error_msg}), 502
    payload = {
        "from_email": item.get("from_email", ""),
        "from_name": item.get("from_name", ""),
        "to": item.get("to", ""),
        "subject": item.get("subject", ""),
        "text": item.get("text", ""),
        "html": item.get("html", ""),
        "reply_to": item.get("reply_to", ""),
        "outbox_id": message_id,
    }
    return _send_local_email(payload, retry_item=item)


# ---- 安全管理 / 多端同步 API ----

@app.route("/api/security/confirm", methods=["POST"])
@login_required
def confirm_sensitive_action():
    data = request.json or {}
    password = data.get("password", "")
    code = data.get("totp_code", "")
    action = data.get("action", "*") or "*"
    username = session.get("username", "")
    if not _verify_admin_credentials(username, password):
        settings = _read_viewer_settings()
        _append_audit(settings, "security.confirm.failed", {"action": action}, success=False)
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": "密码错误"}), 403
    settings = _read_viewer_settings()
    if _totp_enabled(settings) and not _verify_totp_code(_decrypt_setting(settings.get("totp_secret")), code):
        _append_audit(settings, "security.confirm.totp_failed", {"action": action}, success=False)
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": "二次验证码错误"}), 403
    _confirm_sensitive_action(action)
    _append_audit(settings, "security.confirmed", {"action": action})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


@app.route("/api/security/totp/setup", methods=["POST"])
@login_required
def setup_totp():
    confirm = _require_sensitive_confirmation("save_settings")
    if confirm:
        return confirm
    settings = _read_viewer_settings()
    raw = secrets.token_bytes(20)
    secret = base64.b32encode(raw).decode("ascii").rstrip("=")
    settings["totp_secret"] = _encrypt_setting(secret)
    settings["totp_enabled"] = False
    _append_audit(settings, "totp.setup")
    _write_viewer_settings(settings)
    issuer = "Memail"
    account = session.get("username", "admin")
    uri = f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"
    return jsonify({"success": True, "secret": secret, "otpauth_uri": uri})


@app.route("/api/security/totp/enable", methods=["POST"])
@login_required
def enable_totp():
    data = request.json or {}
    settings = _read_viewer_settings()
    secret = _decrypt_setting(settings.get("totp_secret"))
    if not secret:
        return jsonify({"success": False, "message": "请先生成 TOTP 密钥"}), 400
    if not _verify_totp_code(secret, data.get("code", "")):
        return jsonify({"success": False, "message": "验证码错误"}), 400
    settings["totp_enabled"] = True
    _append_audit(settings, "totp.enabled")
    _touch_sync_event(settings, "security.totp.enabled", {})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


@app.route("/api/security/totp/disable", methods=["POST"])
@login_required
def disable_totp():
    confirm = _require_sensitive_confirmation("save_settings")
    if confirm:
        return confirm
    settings = _read_viewer_settings()
    settings["totp_enabled"] = False
    settings.pop("totp_secret", None)
    _append_audit(settings, "totp.disabled")
    _touch_sync_event(settings, "security.totp.disabled", {})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


@app.route("/api/security/sessions", methods=["GET"])
@login_required
def list_sessions():
    settings = _read_viewer_settings()
    current = session.get("session_id", "")
    return jsonify({
        "success": True,
        "current_session_id": current,
        "sessions": _settings_list(settings, "sessions"),
        "totp_enabled": _totp_enabled(settings),
    })


@app.route("/api/security/sessions/<session_id>/revoke", methods=["POST"])
@login_required
def revoke_session(session_id):
    confirm = _require_sensitive_confirmation("revoke_session")
    if confirm:
        return confirm
    settings = _read_viewer_settings()
    for item in _settings_list(settings, "sessions"):
        if item.get("id") == session_id:
            item["revoked"] = True
            item["revoked_at"] = _iso_now()
    _append_audit(settings, "session.revoked", {"session_id": session_id})
    _touch_sync_event(settings, "security.session.revoked", {"session_id": session_id})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


@app.route("/api/security/audit", methods=["GET"])
@login_required
def list_audit_logs():
    settings = _read_viewer_settings()
    limit = min(max(_safe_int(request.args.get("limit"), 100), 1), 300)
    logs = list(reversed(_settings_list(settings, "audit_logs")[-limit:]))
    return jsonify({"success": True, "logs": logs})


@app.route("/api/devices/tokens", methods=["GET"])
@login_required
def list_device_tokens():
    settings = _read_viewer_settings()
    tokens = []
    for item in _settings_list(settings, "device_tokens"):
        tokens.append({k: v for k, v in item.items() if k != "token_hash"})
    return jsonify({"success": True, "tokens": tokens})


@app.route("/api/devices/tokens", methods=["POST"])
@login_required
def create_device_token():
    confirm = _require_sensitive_confirmation("create_device_token")
    if confirm:
        return confirm
    data = request.json or {}
    token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _iso_now()
    item = {
        "id": _new_id("dev_"),
        "name": (data.get("name") or "Device").strip()[:80],
        "token_hash": _hash_token(token),
        "scopes": data.get("scopes") if isinstance(data.get("scopes"), list) else ["sync:read"],
        "created_at": now,
        "last_seen": "",
        "last_ip": "",
        "revoked": False,
    }
    settings = _read_viewer_settings()
    tokens = _settings_list(settings, "device_tokens")
    tokens.append(item)
    settings["device_tokens"] = tokens[-100:]
    _append_audit(settings, "device_token.created", {"id": item["id"], "name": item["name"]})
    _touch_sync_event(settings, "device.token.created", {"id": item["id"]})
    _write_viewer_settings(settings)
    public = {k: v for k, v in item.items() if k != "token_hash"}
    return jsonify({"success": True, "token": token, "device": public})


@app.route("/api/devices/tokens/<token_id>", methods=["DELETE"])
@login_required
def revoke_device_token(token_id):
    confirm = _require_sensitive_confirmation("revoke_device_token")
    if confirm:
        return confirm
    settings = _read_viewer_settings()
    for item in _settings_list(settings, "device_tokens"):
        if item.get("id") == token_id:
            item["revoked"] = True
            item["revoked_at"] = _iso_now()
    _append_audit(settings, "device_token.revoked", {"id": token_id})
    _touch_sync_event(settings, "device.token.revoked", {"id": token_id})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


@app.route("/api/sync/bootstrap", methods=["GET"])
@device_or_login_required
def sync_bootstrap():
    settings = _read_viewer_settings()
    mailboxes = settings.get("mailboxes", []) if isinstance(settings.get("mailboxes"), list) else []
    return jsonify({
        "success": True,
        "server_time": _iso_now(),
        "sync_seq": _safe_int(settings.get("sync_seq"), 0),
        "protocol": {
            "version": 1,
            "conflict": "server-wins",
            "offline_cache": {
                "drafts": "client may edit offline; send full draft with latest version",
                "mail_cache": "client stores immutable message snapshots and asks /api/sync/changes for invalidation events",
            },
        },
        "mailboxes": mailboxes,
        "drafts": [_public_draft(item) for item in _settings_list(settings, "drafts")],
        "outbox": [_public_outbox(item) for item in _settings_list(settings, "outbox")],
        "security": {
            "totp_enabled": _totp_enabled(settings),
            "sessions": _settings_list(settings, "sessions"),
            "device_tokens": [
                {k: v for k, v in item.items() if k != "token_hash"}
                for item in _settings_list(settings, "device_tokens")
            ],
        },
    })


@app.route("/api/sync/changes", methods=["GET"])
@device_or_login_required
def sync_changes():
    settings = _read_viewer_settings()
    since = _safe_int(request.args.get("since"), 0)
    limit = min(max(_safe_int(request.args.get("limit"), SYNC_EVENT_LIMIT), 1), 500)
    events = [event for event in _settings_list(settings, "sync_events") if _safe_int(event.get("seq"), 0) > since]
    return jsonify({
        "success": True,
        "server_time": _iso_now(),
        "sync_seq": _safe_int(settings.get("sync_seq"), 0),
        "events": events[:limit],
        "has_more": len(events) > limit,
    })


@app.route("/api/sync/push", methods=["POST"])
@device_or_login_required
def sync_push():
    data = request.json or {}
    changes = data.get("changes", [])
    if not isinstance(changes, list):
        return jsonify({"success": False, "message": "changes 必须是数组"}), 400
    settings = _read_viewer_settings()
    applied = []
    conflicts = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        ctype = change.get("type")
        payload = change.get("payload") or {}
        if ctype == "draft.upsert":
            drafts = _settings_list(settings, "drafts")
            draft_id = payload.get("id") or _new_id("drf_")
            existing = next((item for item in drafts if item.get("id") == draft_id), None)
            incoming_version = _safe_int(payload.get("version"), 0)
            if existing and incoming_version < _safe_int(existing.get("version"), 0):
                conflicts.append({"id": draft_id, "type": ctype, "reason": "server_has_newer_version"})
                continue
            now = _iso_now()
            if not existing:
                existing = {"id": draft_id, "created_at": now, "version": 0}
                drafts.append(existing)
            existing.update({
                "account_type": payload.get("account_type", existing.get("account_type", "local")),
                "account_id": payload.get("account_id", existing.get("account_id", "")),
                "from_email": payload.get("from_email", existing.get("from_email", "")),
                "from_name": payload.get("from_name", existing.get("from_name", "")),
                "to": payload.get("to", existing.get("to", "")),
                "subject": payload.get("subject", existing.get("subject", "")),
                "text": payload.get("text", existing.get("text", "")),
                "html": payload.get("html", existing.get("html", "")),
                "updated_at": now,
                "version": max(incoming_version, _safe_int(existing.get("version"), 0)) + 1,
            })
            settings["drafts"] = drafts[-500:]
            _touch_sync_event(settings, "draft.upserted", {"id": draft_id})
            applied.append({"id": draft_id, "type": ctype})
        elif ctype == "draft.delete":
            draft_id = payload.get("id", "")
            settings["drafts"] = [item for item in _settings_list(settings, "drafts") if item.get("id") != draft_id]
            _touch_sync_event(settings, "draft.deleted", {"id": draft_id})
            applied.append({"id": draft_id, "type": ctype})
    _write_viewer_settings(settings)
    return jsonify({"success": True, "applied": applied, "conflicts": conflicts, "sync_seq": _safe_int(settings.get("sync_seq"), 0)})


# ---- 发送邮件 API（通过 Resend） ----

def _send_local_email(data: dict, retry_item: dict | None = None):
    resend_api_key = _get_viewer_secret("resend_api_key") or RESEND_API_KEY
    if not resend_api_key:
        settings = _read_viewer_settings()
        message = "未配置 Resend API Key，无法发信"
        if retry_item:
            _update_outbox_item(settings, retry_item.get("id", ""), "failed", message)
            _touch_sync_event(settings, "outbox.failed", {"id": retry_item.get("id")})
        else:
            _store_outbox_failure(settings, data, message, "local")
        _append_audit(settings, "mail.send.failed", {
            "from": data.get("from_email", ""),
            "to": data.get("to", ""),
            "reason": message,
        }, success=False)
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": "未配置 Resend API Key，无法发信"})
    from_email = data.get("from_email", "").strip()
    from_name = data.get("from_name", "").strip()
    to = data.get("to", "").strip()
    subject = data.get("subject", "").strip()
    html = data.get("html", "").strip()
    text = data.get("text", "").strip()
    reply_to = data.get("reply_to", "").strip()

    # 基本校验
    if not from_email:
        return jsonify({"success": False, "message": "请填写发件人邮箱"})
    if not to:
        return jsonify({"success": False, "message": "请填写收件人邮箱"})
    if not subject:
        return jsonify({"success": False, "message": "请填写邮件主题"})
    if not html and not text:
        return jsonify({"success": False, "message": "请填写邮件正文"})
    if not _is_managed_sender(from_email):
        return jsonify({"success": False, "message": "发件人邮箱不存在或密码不匹配，无法发信"})

    # 构造发件人字段
    sender = f"{from_name} <{from_email}>" if from_name else from_email

    # 支持多收件人（逗号分隔）
    to_list = [addr.strip() for addr in to.split(",") if addr.strip()]

    # 构造 Resend API 请求
    payload = {
        "from": sender,
        "to": to_list,
        "subject": subject,
    }
    sanitized_html = _sanitize_email_html(html) if html else ""
    if html:
        payload["html"] = sanitized_html
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        resp = http_session.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if resp.status_code in (200, 201):
            result = resp.json()
            resend_id = result.get("id", "")

            # 存储已发送记录到 mail-server
            try:
                base_url = DUCKMAIL_BASE_URL.rstrip("/")
                http_session.post(
                    f"{base_url}/admin/sent",
                    json={
                        "from_address": from_email.lower(),
                        "to": to_list,
                        "subject": subject,
                        "text": text,
                        "html": sanitized_html,
                        "resend_id": resend_id,
                    },
                    headers={
                        "Authorization": f"Bearer {DUCKMAIL_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    timeout=10,
                )
            except Exception:
                pass  # 存储失败不影响发送结果
            settings = _read_viewer_settings()
            if retry_item:
                _update_outbox_item(settings, retry_item.get("id", ""), "sent")
                _touch_sync_event(settings, "outbox.sent", {"id": retry_item.get("id")})
            _append_audit(settings, "mail.send.success", {"from": from_email, "to": to_list})
            _write_viewer_settings(settings)

            return jsonify({
                "success": True,
                "message": "邮件发送成功",
                "email_id": resend_id,
            })
        else:
            # 解析 Resend 错误信息
            error_msg = "发送失败"
            try:
                err_data = resp.json()
                error_msg = err_data.get("message", "") or err_data.get("name", error_msg)
            except Exception:
                error_msg = f"发送失败 (HTTP {resp.status_code})"
            settings = _read_viewer_settings()
            if retry_item:
                _update_outbox_item(settings, retry_item.get("id", ""), "failed", error_msg)
                _touch_sync_event(settings, "outbox.failed", {"id": retry_item.get("id")})
            else:
                _store_outbox_failure(settings, data, error_msg, "local")
            _append_audit(settings, "mail.send.failed", {"from": from_email, "to": to_list, "reason": error_msg}, success=False)
            _write_viewer_settings(settings)
            return jsonify({"success": False, "message": error_msg})

    except Exception as e:
        app.logger.error(f"发送邮件失败: {e}", exc_info=True)
        settings = _read_viewer_settings()
        message = "服务内部错误，请稍后重试"
        if retry_item:
            _update_outbox_item(settings, retry_item.get("id", ""), "failed", message)
            _touch_sync_event(settings, "outbox.failed", {"id": retry_item.get("id")})
        else:
            _store_outbox_failure(settings, data, message, "local")
        _append_audit(settings, "mail.send.failed", {"from": from_email, "to": to_list, "reason": str(e)}, success=False)
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": message})


@app.route("/api/send", methods=["POST"])
@login_required
def send_email():
    """通过 Resend API 发送邮件"""
    return _send_local_email(request.json or {})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
