import ipaddress
import base64
import hmac
import json
import hashlib
import html as html_lib
import mimetypes
import os
import queue
import threading
import re
import secrets
import socket
import struct
import time
import uuid
import requests
import bleach
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError
from functools import wraps
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from bleach.css_sanitizer import CSSSanitizer
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response, stream_with_context, send_from_directory
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
MAX_SEND_ATTACHMENT_BYTES = int(os.getenv("MAX_SEND_ATTACHMENT_BYTES", str(15 * 1024 * 1024)))
MAX_SEND_ATTACHMENTS_BYTES = int(os.getenv("MAX_SEND_ATTACHMENTS_BYTES", str(25 * 1024 * 1024)))
MAX_IMAGE_PROXY_BYTES = int(os.getenv("MAX_IMAGE_PROXY_BYTES", str(5 * 1024 * 1024)))
IMAGE_PROXY_CONNECT_TIMEOUT_SECONDS = float(os.getenv("IMAGE_PROXY_CONNECT_TIMEOUT_SECONDS", "3"))
IMAGE_PROXY_READ_TIMEOUT_SECONDS = float(os.getenv("IMAGE_PROXY_READ_TIMEOUT_SECONDS", "8"))
IMAGE_PROXY_TOTAL_TIMEOUT_SECONDS = float(os.getenv("IMAGE_PROXY_TOTAL_TIMEOUT_SECONDS", "15"))
AI_TRANSLATION_TIMEOUT_SECONDS = int(os.getenv("AI_TRANSLATION_TIMEOUT_SECONDS", "45"))
AI_TRANSLATION_STREAM_READ_TIMEOUT_SECONDS = float(os.getenv("AI_TRANSLATION_STREAM_READ_TIMEOUT_SECONDS", "15"))
TRANSLATION_SERVICE_TIMEOUT_SECONDS = int(os.getenv("TRANSLATION_SERVICE_TIMEOUT_SECONDS", "12"))
TRANSLATION_CACHE_LIMIT = int(os.getenv("TRANSLATION_CACHE_LIMIT", "500"))
TRANSLATION_CACHE_MAX_CHARS = int(os.getenv("TRANSLATION_CACHE_MAX_CHARS", str(2 * 1024 * 1024)))
TRANSLATION_BATCH_SEPARATOR = "\n<<<MEMAIL_TRANSLATION_SPLIT_9EC1B7>>>\n"
TRANSLATION_BATCH_SIZES = {"baidu": 18, "tencent": 12}
TRANSLATION_BATCH_MAX_TEXT_CHARS = {"baidu": 1500, "tencent": 2000}
TRANSLATION_PROVIDERS = {"ai", "baidu", "tencent", "google_cloud"}
MOBILE_PUSH_POLL_SECONDS = int(os.getenv("MOBILE_PUSH_POLL_SECONDS", "20"))
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongodb:27017").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME") or os.getenv("DB_NAME", "mailserver")
EXTRACTION_SCAN_INTERVAL_SECONDS = int(os.getenv("EXTRACTION_SCAN_INTERVAL_SECONDS", "300"))
EXTRACTION_SCAN_LIMIT = int(os.getenv("EXTRACTION_SCAN_LIMIT", "300"))
EXTRACTION_SCAN_PAGE_SIZE = min(200, max(20, int(os.getenv("EXTRACTION_SCAN_PAGE_SIZE", "100"))))
EXTRACTION_LOOKBACK_PER_SCOPE = min(50, max(0, int(os.getenv("EXTRACTION_LOOKBACK_PER_SCOPE", "8"))))
EXTRACTION_RESULT_LIMIT = int(os.getenv("EXTRACTION_RESULT_LIMIT", "5000"))
EXTRACTION_RESULT_STORE = os.getenv("EXTRACTION_RESULT_STORE", "mongo").strip().lower()
_MOBILE_EVENT_SUBSCRIBERS: set[queue.Queue] = set()
_MOBILE_EVENT_LOCK = threading.Lock()
_MOBILE_EVENT_MONITOR_STARTED = False
_MOBILE_EVENT_LAST_SEQ = 0
_MOBILE_EVENT_LAST_FINGERPRINT = ""
_EXTRACTION_SCAN_STARTED = False
_EXTRACTION_SCAN_RUNNING = False
_EXTRACTION_SCAN_LOCK = threading.Lock()
_EXTRACTION_MONGO_CLIENT = None
_EXTRACTION_MONGO_READY = False
_EXTRACTION_MIGRATION_DONE = False
_EXTRACTION_MONGO_LOCK = threading.Lock()
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


def _safe_translation_settings(translation: dict) -> dict:
    baidu = translation.get("baidu", {}) if isinstance(translation.get("baidu"), dict) else {}
    tencent = translation.get("tencent", {}) if isinstance(translation.get("tencent"), dict) else {}
    google_cloud = translation.get("google_cloud", {}) if isinstance(translation.get("google_cloud"), dict) else {}
    return {
        "default_provider": translation.get("default_provider", "ai"),
        "fallback_to_ai": bool(translation.get("fallback_to_ai", True)),
        "baidu": {
            "appid": baidu.get("appid", ""),
            "secret_configured": bool(_decrypt_setting(baidu.get("secret"))),
        },
        "tencent": {
            "secret_id": tencent.get("secret_id", ""),
            "secret_key_configured": bool(_decrypt_setting(tencent.get("secret_key"))),
            "region": tencent.get("region", "ap-guangzhou"),
        },
        "google_cloud": {
            "api_key_configured": bool(_decrypt_setting(google_cloud.get("api_key"))),
        },
    }


def _get_translation_settings() -> dict:
    settings = _read_viewer_settings()
    translation = settings.get("translation", {})
    if not isinstance(translation, dict):
        translation = {}
    default_provider = (translation.get("default_provider") or "ai").strip().lower()
    if default_provider not in TRANSLATION_PROVIDERS:
        default_provider = "ai"
    baidu = translation.get("baidu", {}) if isinstance(translation.get("baidu"), dict) else {}
    tencent = translation.get("tencent", {}) if isinstance(translation.get("tencent"), dict) else {}
    google_cloud = translation.get("google_cloud", {}) if isinstance(translation.get("google_cloud"), dict) else {}
    translation["default_provider"] = default_provider
    translation["fallback_to_ai"] = bool(translation.get("fallback_to_ai", True))
    translation["baidu"] = baidu
    translation["tencent"] = {
        **tencent,
        "region": (tencent.get("region") or "ap-guangzhou").strip() or "ap-guangzhou",
    }
    translation["google_cloud"] = google_cloud
    return translation


def _write_translation_settings(data: dict) -> dict:
    settings = _read_viewer_settings()
    current = settings.get("translation", {}) if isinstance(settings.get("translation"), dict) else {}
    default_provider = (data.get("default_provider") or current.get("default_provider") or "ai").strip().lower()
    if default_provider not in TRANSLATION_PROVIDERS:
        raise ValueError("请选择有效翻译渠道")
    current["default_provider"] = default_provider
    current["fallback_to_ai"] = bool(data.get("fallback_to_ai", current.get("fallback_to_ai", True)))

    baidu = current.get("baidu", {}) if isinstance(current.get("baidu"), dict) else {}
    if "baidu_appid" in data:
        baidu["appid"] = (data.get("baidu_appid") or "").strip()
    if data.get("clear_baidu_secret"):
        baidu.pop("secret", None)
    elif data.get("baidu_secret"):
        baidu["secret"] = _encrypt_setting((data.get("baidu_secret") or "").strip())
    current["baidu"] = baidu

    tencent = current.get("tencent", {}) if isinstance(current.get("tencent"), dict) else {}
    if "tencent_secret_id" in data:
        tencent["secret_id"] = (data.get("tencent_secret_id") or "").strip()
    if "tencent_region" in data:
        tencent["region"] = (data.get("tencent_region") or "ap-guangzhou").strip() or "ap-guangzhou"
    if data.get("clear_tencent_secret_key"):
        tencent.pop("secret_key", None)
    elif data.get("tencent_secret_key"):
        tencent["secret_key"] = _encrypt_setting((data.get("tencent_secret_key") or "").strip())
    current["tencent"] = tencent

    google_cloud = current.get("google_cloud", {}) if isinstance(current.get("google_cloud"), dict) else {}
    if data.get("clear_google_cloud_api_key"):
        google_cloud.pop("api_key", None)
    elif data.get("google_cloud_api_key"):
        google_cloud["api_key"] = _encrypt_setting((data.get("google_cloud_api_key") or "").strip())
    current["google_cloud"] = google_cloud

    settings["translation"] = current
    _write_viewer_settings(settings)
    return current


def _translation_request_identity(data: dict) -> dict:
    return {
        "account_type": str(data.get("account_type") or data.get("accountType") or "").strip().lower(),
        "account_id": str(data.get("account_id") or data.get("accountId") or "").strip().lower(),
        "folder": str(data.get("folder") or "").strip() or "inbox",
        "message_id": str(data.get("message_id") or data.get("messageId") or data.get("id") or "").strip(),
    }


def _translation_source_hash(subject: str, html_content: str, text_content: str) -> str:
    payload = {
        "subject": subject or "",
        "html": html_content or "",
        "text": text_content or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _translation_cache_variant(provider: str) -> str:
    provider = (provider or "ai").strip().lower()
    if provider != "ai":
        return provider
    ai = _get_ai_settings()
    default_model = ai.get("default_model", {}) if isinstance(ai.get("default_model"), dict) else {}
    return "ai:%s:%s" % (
        str(default_model.get("channel_id") or "").strip(),
        str(default_model.get("model") or "").strip(),
    )


def _translation_cache_key(identity: dict, provider: str, source_hash: str) -> str:
    payload = {
        "v": 1,
        "variant": _translation_cache_variant(provider),
        "source_hash": source_hash,
        "account_type": identity.get("account_type", ""),
        "account_id": identity.get("account_id", ""),
        "folder": identity.get("folder", ""),
        "message_id": identity.get("message_id", ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _translation_cache_items(settings: dict) -> dict:
    cache = settings.get("translation_cache", {})
    if not isinstance(cache, dict):
        return {}
    items = cache.get("items", {})
    return items if isinstance(items, dict) else {}


def _read_translation_cache_item(settings: dict, cache_key: str) -> dict | None:
    item = _translation_cache_items(settings).get(cache_key)
    if not isinstance(item, dict) or not item.get("translation"):
        return None
    return item


def _write_translation_cache_item(settings: dict, cache_key: str, item: dict) -> bool:
    if len(item.get("translation", "")) > TRANSLATION_CACHE_MAX_CHARS:
        app.logger.info(
            "邮件翻译缓存跳过: output_chars=%s max_chars=%s",
            len(item.get("translation", "")),
            TRANSLATION_CACHE_MAX_CHARS,
        )
        return False
    cache = settings.get("translation_cache", {})
    if not isinstance(cache, dict):
        cache = {}
    items = cache.get("items", {})
    if not isinstance(items, dict):
        items = {}
    items[cache_key] = item
    if TRANSLATION_CACHE_LIMIT > 0 and len(items) > TRANSLATION_CACHE_LIMIT:
        sorted_items = sorted(
            items.items(),
            key=lambda kv: str((kv[1] or {}).get("last_used_at") or (kv[1] or {}).get("created_at") or ""),
            reverse=True,
        )
        items = dict(sorted_items[:TRANSLATION_CACHE_LIMIT])
    settings["translation_cache"] = {
        "version": 1,
        "limit": TRANSLATION_CACHE_LIMIT,
        "items": items,
    }
    _write_viewer_settings(settings)
    return True


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


def _translation_text_content(html_content: str, text_content: str, subject: str) -> str:
    if html_content:
        body = _extract_visible_text_from_html(html_content)
    else:
        body = _normalize_translation_source_text(text_content)
    if subject:
        body = f"邮件主题：{subject}\n\n{body}"
    return body.strip()


def _html_text_needs_translation(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    if re.fullmatch(r"[\W\d_]+", value, flags=re.UNICODE):
        return False
    if re.fullmatch(r"https?://\S+|mailto:\S+|[\w.+-]+@[\w.-]+\.\w+", value, flags=re.IGNORECASE):
        return False
    return any(ch.isalpha() for ch in value)


def _split_translation_chunks(value: str, max_chars: int = 1800) -> list[str]:
    text = value or ""
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    parts = re.split(r"(?<=[。！？.!?；;])(\s+)", text)
    merged_parts = []
    for index in range(0, len(parts), 2):
        piece = parts[index]
        if index + 1 < len(parts):
            piece += parts[index + 1]
        if piece:
            merged_parts.append(piece)
    if not merged_parts:
        merged_parts = [text]
    for piece in merged_parts:
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(piece), max_chars):
                chunks.append(piece[start:start + max_chars])
            continue
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _translate_text_with_chunks(value: str, translate_text) -> str:
    chunks = _split_translation_chunks(value)
    if not chunks:
        return ""
    if len(chunks) == 1:
        return translate_text(chunks[0])
    translated = [translate_text(chunk) for chunk in chunks]
    separator = "\n" if "\n" in value else " "
    return separator.join(part.strip() for part in translated if part.strip()).strip()


def _translate_texts_with_provider(texts: list[str], translate_text, provider: str) -> dict[str, str]:
    unique_texts: list[str] = []
    seen: set[str] = set()
    for text in texts:
        source = str(text or "")
        if not source or source in seen:
            continue
        seen.add(source)
        unique_texts.append(source)
    if not unique_texts:
        return {}

    results: dict[str, str] = {}
    batch_size = max(1, TRANSLATION_BATCH_SIZES.get(provider, 8))
    max_text_chars = max(200, TRANSLATION_BATCH_MAX_TEXT_CHARS.get(provider, 1200))
    pending: list[str] = []

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        group = pending
        pending = []
        try:
            joined = TRANSLATION_BATCH_SEPARATOR.join(group)
            translated_joined = translate_text(joined)
            parts = translated_joined.split(TRANSLATION_BATCH_SEPARATOR)
            if len(parts) != len(group):
                raise RuntimeError(f"批量翻译分隔符未保留: expected={len(group)} actual={len(parts)}")
            for source, translated in zip(group, parts):
                results[source] = translated.strip()
            return
        except Exception as exc:
            app.logger.warning(
                "批量翻译失败，降级单条: provider=%s items=%s error=%s",
                provider,
                len(group),
                exc,
            )
        for source in group:
            results[source] = _translate_text_with_chunks(source, translate_text)

    for source in unique_texts:
        if len(source) > max_text_chars:
            flush_pending()
            results[source] = _translate_text_with_chunks(source, translate_text)
            continue
        pending.append(source)
        if len(pending) >= batch_size:
            flush_pending()
    flush_pending()
    return results


class _HtmlTextNodeMasker(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe"}
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.nodes: list[dict] = []
        self.skip_depth = 0

    def _is_hidden_start(self, raw: str) -> bool:
        raw_lower = (raw or "").lower()
        if re.search(r"\shidden(?:[\s=>/]|$)", raw_lower):
            return True
        style_match = re.search(r"\bstyle\s*=\s*(['\"])(.*?)\1", raw, flags=re.IGNORECASE | re.DOTALL)
        if not style_match:
            return False
        style = re.sub(r"\s+", "", html_lib.unescape(style_match.group(2)).lower())
        return "display:none" in style or "visibility:hidden" in style

    def _append_raw_start(self, tag: str, is_void: bool = False) -> None:
        raw = self.get_starttag_text() or f"<{tag}>"
        self.parts.append(raw)
        if self.skip_depth and not is_void:
            self.skip_depth += 1
            return
        if tag in self.SKIP_TAGS or self._is_hidden_start(raw):
            if not is_void:
                self.skip_depth = 1

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self._append_raw_start(tag, tag in self.VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs) -> None:
        raw = self.get_starttag_text() or f"<{tag}/>"
        self.parts.append(raw)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        self.parts.append(f"</{tag}>")
        if self.skip_depth:
            self.skip_depth = max(0, self.skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            self.parts.append(html_lib.escape(data, quote=False))
            return
        if not data or not data.strip():
            self.parts.append(data or "")
            return
        leading = re.match(r"^\s*", data).group(0)
        trailing = re.search(r"\s*$", data).group(0)
        core = data[len(leading):len(data) - len(trailing) if trailing else len(data)]
        if not _html_text_needs_translation(core):
            self.parts.append(html_lib.escape(data, quote=False))
            return
        token = f"\ue000{len(self.nodes)}\ue001"
        self.nodes.append({"token": token, "text": core})
        self.parts.append(f"{leading}{token}{trailing}")

    def handle_comment(self, data: str) -> None:
        return

    def masked_html(self) -> str:
        return "".join(self.parts)


class _HtmlSemanticBlockMasker(HTMLParser):
    BLOCK_TAGS = {
        "p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "caption", "dt", "dd", "pre", "a", "button",
    }
    CONTAINER_TAGS = {"div", "section", "article", "header", "footer", "center"}
    SKIP_TAGS = _HtmlTextNodeMasker.SKIP_TAGS
    VOID_TAGS = _HtmlTextNodeMasker.VOID_TAGS

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocks: list[dict] = []
        self.skip_depth = 0
        self.stack: list[dict] = []

    def _is_hidden_start(self, raw: str) -> bool:
        return _HtmlTextNodeMasker()._is_hidden_start(raw)

    def _append(self, value: str) -> None:
        if self.stack:
            self.stack[-1]["parts"].append(value)
        else:
            self.parts.append(value)

    def _start_block(self, tag: str, raw: str, attrs) -> None:
        self.stack.append({
            "tag": tag,
            "parts": [raw],
            "text": [],
            "link_href": dict((str(k).lower(), str(v or "")) for k, v in attrs).get("href", ""),
        })

    def _finish_block(self, tag: str) -> bool:
        if not self.stack or self.stack[-1]["tag"] != tag:
            return False
        block = self.stack.pop()
        block["parts"].append(f"</{tag}>")
        html = "".join(block["parts"])
        text = _normalize_translation_source_text(" ".join(block["text"]))
        if _html_text_needs_translation(text):
            token = f"\ue100{len(self.blocks)}\ue101"
            self.blocks.append({"token": token, "html": html, "text": text, "tag": tag, "href": block.get("link_href", "")})
            self._append(token)
        else:
            self._append(html)
        return True

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        raw = self.get_starttag_text() or f"<{tag}>"
        is_void = tag in self.VOID_TAGS
        if self.skip_depth:
            self._append(raw)
            if not is_void:
                self.skip_depth += 1
            return
        if tag in self.SKIP_TAGS or self._is_hidden_start(raw):
            self._append(raw)
            if not is_void:
                self.skip_depth = 1
            return
        if self.stack:
            self.stack[-1]["parts"].append(raw)
            return
        if tag in self.BLOCK_TAGS:
            self._start_block(tag, raw, attrs)
            return
        self.parts.append(raw)

    def handle_startendtag(self, tag: str, attrs) -> None:
        raw = self.get_starttag_text() or f"<{tag}/>"
        self._append(raw)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self._append(f"</{tag}>")
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self._finish_block(tag):
            return
        self._append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            self._append(html_lib.escape(data, quote=False))
            return
        if self.stack:
            self.stack[-1]["parts"].append(html_lib.escape(data, quote=False))
            if _html_text_needs_translation(data):
                self.stack[-1]["text"].append(data)
            return
        self._append(html_lib.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def masked_html(self) -> str:
        while self.stack:
            block = self.stack.pop(0)
            self.parts.extend(block.get("parts", []))
        return "".join(self.parts)


def _mask_html_text_nodes(html: str) -> tuple[str, list[dict]]:
    sanitized = _sanitize_email_html(html)
    if not sanitized:
        return "", []
    parser = _HtmlTextNodeMasker()
    try:
        parser.feed(sanitized)
        parser.close()
    except Exception:
        app.logger.warning("HTML 文本节点提取失败，退回纯文本翻译", exc_info=True)
        return "", []
    return parser.masked_html(), parser.nodes


def _mask_html_semantic_blocks(html: str) -> tuple[str, list[dict]]:
    sanitized = _sanitize_email_html(html)
    if not sanitized:
        return "", []
    parser = _HtmlSemanticBlockMasker()
    try:
        parser.feed(sanitized)
        parser.close()
    except Exception:
        app.logger.warning("HTML 语义块提取失败，退回文字节点翻译", exc_info=True)
        return "", []
    return parser.masked_html(), parser.blocks


def _block_translation_to_html(block: dict, translated: str) -> str:
    tag = block.get("tag", "")
    text = html_lib.escape((translated or "").strip(), quote=False)
    if not text:
        return block.get("html", "")
    if tag == "a":
        href = html_lib.escape(block.get("href", ""), quote=True)
        return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{text}</a>' if href else f"<a>{text}</a>"
    if tag == "pre":
        return f"<pre>{text}</pre>"
    return f"<{tag}>{text}</{tag}>" if tag else text


def _translate_html_semantic_blocks(html: str, translate_text, provider: str) -> tuple[str, int, int]:
    masked_html, blocks = _mask_html_semantic_blocks(html)
    if not masked_html or not blocks:
        return "", 0, 0
    translated_cache: dict[str, str] = {}
    replacements = {}
    started_at = time.time()
    for block in blocks:
        source = block["text"]
        if source not in translated_cache:
            translated_cache[source] = _translate_text_with_chunks(source, translate_text)
        replacements[block["token"]] = _block_translation_to_html(block, translated_cache[source])
    translated_html = masked_html
    for token, translated in replacements.items():
        translated_html = translated_html.replace(token, translated)
    missing = [token for token in replacements if token in translated_html]
    if missing:
        raise RuntimeError(f"HTML 语义块回填失败，仍有 {len(missing)} 个本地占位未替换")
    output = _prepare_html_for_render(translated_html)
    app.logger.info(
        "HTML 语义块翻译完成: provider=%s blocks=%s unique_blocks=%s output_chars=%s elapsed_ms=%s",
        provider,
        len(blocks),
        len(translated_cache),
        len(output),
        int((time.time() - started_at) * 1000),
    )
    return output, len(blocks), sum(len(block.get("text", "")) for block in blocks)


def _translate_html_text_nodes(html: str, translate_text, provider: str, translate_texts=None) -> tuple[str, int, int]:
    masked_html, nodes = _mask_html_text_nodes(html)
    if not masked_html or not nodes:
        return "", 0, 0
    sources = [node["text"] for node in nodes]
    if translate_texts:
        translated_cache = translate_texts(sources)
    else:
        translated_cache = _translate_texts_with_provider(sources, translate_text, provider)
    replacements = {}
    started_at = time.time()
    for node in nodes:
        source = node["text"]
        replacements[node["token"]] = html_lib.escape(translated_cache.get(source, source), quote=False)
    translated_html = masked_html
    for token, translated in replacements.items():
        translated_html = translated_html.replace(token, translated)
    missing = [token for token in replacements if token in translated_html]
    if missing:
        raise RuntimeError(f"HTML 翻译回填失败，仍有 {len(missing)} 个本地占位未替换")
    output = _prepare_html_for_render(translated_html)
    app.logger.info(
        "HTML 保壳翻译完成: provider=%s nodes=%s unique_nodes=%s output_chars=%s elapsed_ms=%s",
        provider,
        len(nodes),
        len(translated_cache),
        len(output),
        int((time.time() - started_at) * 1000),
    )
    return output, len(nodes), sum(len(node.get("text", "")) for node in nodes)


def _call_baidu_translate(text: str, settings: dict) -> str:
    baidu = settings.get("baidu", {}) if isinstance(settings.get("baidu"), dict) else {}
    appid = (baidu.get("appid") or "").strip()
    secret = _decrypt_setting(baidu.get("secret"))
    if not appid or not secret:
        raise RuntimeError("百度翻译未配置 APPID 或密钥")
    salt = str(int(time.time() * 1000)) + secrets.token_hex(4)
    sign = hashlib.md5(f"{appid}{text}{salt}{secret}".encode("utf-8")).hexdigest()
    started_at = time.time()
    resp = fast_http_session.post(
        "https://fanyi-api.baidu.com/api/trans/vip/translate",
        data={
            "q": text,
            "from": "auto",
            "to": "zh",
            "appid": appid,
            "salt": salt,
            "sign": sign,
        },
        timeout=TRANSLATION_SERVICE_TIMEOUT_SECONDS,
    )
    app.logger.info(
        "百度翻译响应: status=%s input_chars=%s elapsed_ms=%s",
        resp.status_code,
        len(text),
        int((time.time() - started_at) * 1000),
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"百度翻译返回非 JSON: {_translation_preview(resp.text, 300)}") from exc
    if resp.status_code >= 400 or payload.get("error_code"):
        raise RuntimeError(payload.get("error_msg") or payload.get("error_code") or f"百度翻译 HTTP {resp.status_code}")
    items = payload.get("trans_result") or []
    translated = "\n".join(str(item.get("dst") or "") for item in items if isinstance(item, dict)).strip()
    if not translated:
        raise RuntimeError("百度翻译返回空内容")
    return translated


def _tencent_hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _call_tencent_translate(text: str, settings: dict) -> str:
    tencent = settings.get("tencent", {}) if isinstance(settings.get("tencent"), dict) else {}
    secret_id = (tencent.get("secret_id") or "").strip()
    secret_key = _decrypt_setting(tencent.get("secret_key"))
    region = (tencent.get("region") or "ap-guangzhou").strip() or "ap-guangzhou"
    if not secret_id or not secret_key:
        raise RuntimeError("腾讯翻译未配置 SecretId 或 SecretKey")
    service = "tmt"
    host = "tmt.tencentcloudapi.com"
    action = "TextTranslate"
    version = "2018-03-21"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    payload = json.dumps({
        "SourceText": text,
        "Source": "auto",
        "Target": "zh",
        "ProjectId": 0,
    }, ensure_ascii=False, separators=(",", ":"))
    canonical_request = "\n".join([
        "POST",
        "/",
        "",
        f"content-type:application/json; charset=utf-8\nhost:{host}\n",
        "content-type;host",
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    ])
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(timestamp),
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    secret_date = _tencent_hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _tencent_hmac_sha256(secret_date, service)
    secret_signing = _tencent_hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        "SignedHeaders=content-type;host, "
        f"Signature={signature}"
    )
    started_at = time.time()
    resp = fast_http_session.post(
        f"https://{host}",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": version,
            "X-TC-Region": region,
        },
        timeout=TRANSLATION_SERVICE_TIMEOUT_SECONDS,
    )
    app.logger.info(
        "腾讯翻译响应: status=%s input_chars=%s elapsed_ms=%s",
        resp.status_code,
        len(text),
        int((time.time() - started_at) * 1000),
    )
    try:
        payload_data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"腾讯翻译返回非 JSON: {_translation_preview(resp.text, 300)}") from exc
    response = payload_data.get("Response", {}) if isinstance(payload_data, dict) else {}
    if resp.status_code >= 400 or response.get("Error"):
        error = response.get("Error") or {}
        raise RuntimeError(error.get("Message") or error.get("Code") or f"腾讯翻译 HTTP {resp.status_code}")
    translated = (response.get("TargetText") or "").strip()
    if not translated:
        raise RuntimeError("腾讯翻译返回空内容")
    return translated


def _call_google_cloud_translate_batch(texts: list[str], settings: dict) -> dict[str, str]:
    google_cloud = settings.get("google_cloud", {}) if isinstance(settings.get("google_cloud"), dict) else {}
    api_key = _decrypt_setting(google_cloud.get("api_key"))
    if not api_key:
        raise RuntimeError("Google Cloud Translation 未配置 API Key")
    unique_texts: list[str] = []
    seen: set[str] = set()
    for text in texts:
        source = str(text or "")
        if not source or source in seen:
            continue
        seen.add(source)
        unique_texts.append(source)
    if not unique_texts:
        return {}
    results: dict[str, str] = {}
    for start in range(0, len(unique_texts), 128):
        batch = unique_texts[start:start + 128]
        started_at = time.time()
        resp = fast_http_session.post(
            "https://translation.googleapis.com/language/translate/v2",
            params={"key": api_key},
            json={
                "q": batch,
                "target": "zh-CN",
                "format": "text",
            },
            timeout=TRANSLATION_SERVICE_TIMEOUT_SECONDS,
        )
        app.logger.info(
            "Google Cloud Translation 响应: status=%s items=%s input_chars=%s elapsed_ms=%s",
            resp.status_code,
            len(batch),
            sum(len(text) for text in batch),
            int((time.time() - started_at) * 1000),
        )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Google Cloud Translation 返回非 JSON: {_translation_preview(resp.text, 300)}") from exc
        if resp.status_code >= 400 or payload.get("error"):
            error = payload.get("error") if isinstance(payload, dict) else {}
            message = error.get("message") if isinstance(error, dict) else ""
            raise RuntimeError(message or f"Google Cloud Translation HTTP {resp.status_code}")
        items = ((payload.get("data") or {}).get("translations") or []) if isinstance(payload, dict) else []
        if len(items) != len(batch):
            raise RuntimeError(f"Google Cloud Translation 返回数量不匹配: expected={len(batch)} actual={len(items)}")
        for source, item in zip(batch, items):
            translated = html_lib.unescape(str((item or {}).get("translatedText") or "")).strip()
            if translated:
                results[source] = translated
    return results


def _call_google_cloud_translate(text: str, settings: dict) -> str:
    results = _call_google_cloud_translate_batch([text], settings)
    translated = results.get(text, "")
    if not translated:
        raise RuntimeError("Google Cloud Translation 返回空内容")
    return translated


class _MailVisibleTextParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "head", "meta", "title", "link"}
    VOID_SKIP_TAGS = {"meta", "link"}
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "caption", "center", "div", "dt", "dd",
        "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
        "li", "main", "nav", "p", "pre", "section", "table", "tbody", "thead", "tfoot", "tr", "ul", "ol",
    }
    CELL_TAGS = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.in_pre = False

    def _append(self, value: str) -> None:
        if not value:
            return
        if self.parts and not self.parts[-1].endswith(("\n", " ", "\t", "|")) and not value.startswith(("\n", " ", "\t", "|")):
            self.parts.append(" ")
        self.parts.append(value)

    def _newline(self) -> None:
        if not self.parts:
            return
        joined_tail = "".join(self.parts[-3:])
        if joined_tail.endswith("\n\n"):
            return
        if joined_tail.endswith("\n"):
            self.parts.append("\n")
        else:
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.skip_depth:
            if tag not in self.VOID_SKIP_TAGS:
                self.skip_depth += 1
            return
        if tag in self.VOID_SKIP_TAGS:
            return
        if tag in self.SKIP_TAGS:
            self.skip_depth = 1
            return
        attr_map = {str(k).lower(): str(v or "") for k, v in attrs}
        style = attr_map.get("style", "").replace(" ", "").lower()
        if attr_map.get("hidden") is not None or "display:none" in style or "visibility:hidden" in style:
            self.skip_depth = 1
            return
        if tag == "pre":
            self.in_pre = True
        if tag in self.BLOCK_TAGS:
            self._newline()
        if tag in self.CELL_TAGS:
            self._append(" | ")
        if tag == "a":
            href = attr_map.get("href", "").strip()
            if href and href.lower().startswith("mailto:"):
                self._append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "pre":
            self.in_pre = False
        if tag in self.BLOCK_TAGS or tag in self.CELL_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data if self.in_pre else re.sub(r"[ \t\r\f\v]+", " ", data)
        if text.strip():
            self._append(text)

    def text(self) -> str:
        value = "".join(self.parts)
        value = html_lib.unescape(value)
        value = re.sub(r"[ \t]*\|[ \t]*", " | ", value)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _extract_visible_text_from_html(html: str) -> str:
    html = (html or "").strip()
    if not html:
        return ""
    body_match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        html = body_match.group(1)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"(?is)<img\b[^>]*>", " ", html)
    parser = _MailVisibleTextParser()
    try:
        parser.feed(html)
        parser.close()
        text = parser.text()
    except Exception:
        text = _extract_plain_text(html)
    return _normalize_translation_source_text(text)


def _normalize_translation_source_text(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = value.replace("\u00a0", " ")
    value = re.sub(r"https?://\S{120,}", "[link]", value)
    value = re.sub(r"data:image/[^ \n]+", "[image]", value, flags=re.IGNORECASE)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    lines = [line.strip() for line in value.splitlines()]
    compact_lines: list[str] = []
    blank = 0
    previous = ""
    for line in lines:
        if not line:
            blank += 1
            if blank <= 1:
                compact_lines.append("")
            continue
        blank = 0
        if line == previous and len(line) > 12:
            continue
        compact_lines.append(line)
        previous = line
    return "\n".join(compact_lines).strip()


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


def _read_openai_chat_stream(resp: requests.Response, base_url: str, model: str, wants_html: bool, input_chars: int, started_at: float) -> str:
    chunks: list[str] = []
    first_delta_ms: int | None = None
    event_count = 0
    deadline = started_at + max(1, AI_TRANSLATION_TIMEOUT_SECONDS)
    for raw_line in resp.iter_lines(decode_unicode=True):
        if time.time() > deadline:
            raise requests.Timeout(f"AI stream exceeded {AI_TRANSLATION_TIMEOUT_SECONDS}s")
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        event_count += 1
        try:
            payload = json.loads(data)
        except ValueError:
            app.logger.warning("AI 流式响应非 JSON 行: %s", _translation_preview(data, 240))
            continue
        choices = payload.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            if first_delta_ms is None:
                first_delta_ms = int((time.time() - started_at) * 1000)
                app.logger.info(
                    "AI 流式首包(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s first_delta_ms=%s",
                    base_url,
                    model,
                    wants_html,
                    input_chars,
                    first_delta_ms,
                )
            chunks.append(content)
    output = "".join(chunks).strip()
    app.logger.info(
        "AI 流式完成(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s output_chars=%s events=%s first_delta_ms=%s elapsed_ms=%s",
        base_url,
        model,
        wants_html,
        input_chars,
        len(output),
        event_count,
        first_delta_ms,
        int((time.time() - started_at) * 1000),
    )
    return output


def _read_openai_chat_json_response(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError as exc:
        snippet = re.sub(r"\s+", " ", (resp.text or "").replace("\r", " ").replace("\n", " ")).strip()[:500]
        raise RuntimeError(f"AI 服务返回了非 JSON 内容: {snippet}") from exc
    return (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


def _openai_chat_payload(system_prompt: str, content: str, model: str, stream: bool) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "stream": stream,
    }


def _response_mentions_unsupported_stream(resp: requests.Response) -> bool:
    try:
        body = resp.text or ""
    except Exception:
        body = ""
    value = body.lower()
    return resp.status_code in {400, 404, 422} and "stream" in value and any(
        marker in value
        for marker in ("not support", "unsupported", "not implemented", "invalid", "不能", "不支持")
    )


def _call_openai_chat_blocking(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    content: str,
    wants_html: bool,
    input_chars: int,
) -> str:
    started_at = time.time()
    app.logger.info(
        "AI 非流式请求发送(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s timeout=%s",
        base_url,
        model,
        wants_html,
        input_chars,
        AI_TRANSLATION_TIMEOUT_SECONDS,
    )
    resp = fast_http_session.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_openai_chat_payload(system_prompt, content, model, stream=False),
        timeout=AI_TRANSLATION_TIMEOUT_SECONDS,
    )
    app.logger.info(
        "AI 非流式响应完成(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s status=%s elapsed_ms=%s",
        base_url,
        model,
        wants_html,
        input_chars,
        resp.status_code,
        int((time.time() - started_at) * 1000),
    )
    if resp.status_code >= 400:
        raise RuntimeError(_response_error(resp, f"翻译失败 HTTP {resp.status_code}"))
    return _read_openai_chat_json_response(resp)


def _call_openai_chat(channel: dict, api_key: str, model: str, content: str, wants_html: bool = False) -> str:
    base_url = channel.get("base_url", "").rstrip("/")
    if not base_url:
        raise RuntimeError("AI 渠道 Base URL 为空，请重新配置渠道")
    system_prompt = (
        "你是专业邮件翻译助手。请把用户提供的邮件 HTML 翻译为简体中文。"
        "必须保留原始 HTML 标签结构、表格、链接、图片、样式和布局；只翻译可见文字。"
        "不要解释，不要 Markdown，不要代码块，只输出翻译后的完整 HTML。"
        if wants_html else
        "你是专业邮件翻译助手。请把用户提供的邮件正文翻译为简体中文。"
        "保留段落、列表、金额、日期、订单号、邮箱、链接文本、品牌名和专有名词；不要解释，只输出译文。"
    )
    request_started_at = time.time()
    input_chars = len(content or "")
    app.logger.info(
        "AI 请求发送(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s stream=true timeout=%s stream_read_timeout=%s",
        base_url,
        model,
        wants_html,
        input_chars,
        AI_TRANSLATION_TIMEOUT_SECONDS,
        AI_TRANSLATION_STREAM_READ_TIMEOUT_SECONDS,
    )
    with fast_http_session.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_openai_chat_payload(system_prompt, content, model, stream=True),
        timeout=(8, AI_TRANSLATION_STREAM_READ_TIMEOUT_SECONDS),
        stream=True,
    ) as resp:
        app.logger.info(
            "AI 响应已建立(openai_compatible/openai): base_url=%s model=%s wants_html=%s input_chars=%s status=%s elapsed_ms=%s",
            base_url,
            model,
            wants_html,
            input_chars,
            resp.status_code,
            int((time.time() - request_started_at) * 1000),
        )
        if resp.status_code >= 400:
            if _response_mentions_unsupported_stream(resp):
                app.logger.info(
                    "AI 渠道不支持流式，自动切换非流式(openai_compatible/openai): base_url=%s model=%s status=%s",
                    base_url,
                    model,
                    resp.status_code,
                )
                return _call_openai_chat_blocking(base_url, api_key, model, system_prompt, content, wants_html, input_chars)
            raise RuntimeError(_response_error(resp, f"翻译失败 HTTP {resp.status_code}"))
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in content_type:
            app.logger.info(
                "AI 响应不是 SSE，按普通 JSON 解析(openai_compatible/openai): base_url=%s model=%s content_type=%s",
                base_url,
                model,
                content_type,
            )
            return _read_openai_chat_json_response(resp)
        return _read_openai_chat_stream(resp, base_url, model, wants_html, input_chars, request_started_at)


def _call_gemini(channel: dict, api_key: str, model: str, content: str, wants_html: bool = False) -> str:
    base_url = channel.get("base_url", "").rstrip("/")
    if not base_url:
        raise RuntimeError("AI 渠道 Base URL 为空，请重新配置渠道")
    model_name = model if model.startswith("models/") else f"models/{model}"
    prompt = (
        "请把下面邮件 HTML 翻译为简体中文。必须保留原始 HTML 标签结构、表格、链接、图片、样式和布局；"
        "只翻译可见文字。不要解释，不要 Markdown，不要代码块，只输出翻译后的完整 HTML。\n\n"
        if wants_html else
        "请把下面邮件正文翻译为简体中文。保留段落、列表、金额、日期、订单号、邮箱、链接文本、品牌名和专有名词；"
        "不要解释，只输出译文。\n\n"
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
            "generationConfig": {"temperature": 0},
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


def _call_ai_translation_engine(content: str, wants_html: bool) -> tuple[str, dict]:
    ai = _get_ai_settings()
    default_model = ai.get("default_model", {})
    channel = next((item for item in ai.get("channels", []) if item.get("id") == default_model.get("channel_id")), None)
    model = default_model.get("model", "")
    if not channel or not model:
        raise RuntimeError("请先在 AI 设置中配置默认模型")
    api_key = _decrypt_setting(channel.get("api_key"))
    if not api_key:
        raise RuntimeError("默认渠道 API Key 不可用，请重新新增渠道")
    app.logger.info(
        "邮件翻译准备请求: engine=ai provider=%s model=%s base_url=%s wants_html=%s input_chars=%s",
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
    return translated, {
        "engine": "ai",
        "provider": channel.get("provider", ""),
        "model": model,
        "format": "html" if wants_html else "text",
    }


def _call_configured_translation_engine(
    provider: str,
    html_content: str,
    text_content: str,
    subject: str,
    settings: dict,
) -> tuple[str, dict]:
    if provider == "baidu":
        if html_content:
            def translator(text: str) -> str:
                return _call_baidu_translate(text, settings)
            translated_html, node_count, input_chars = _translate_html_text_nodes(
                html_content,
                translator,
                "baidu",
                lambda texts: _translate_texts_with_provider(texts, translator, "baidu"),
            )
            if translated_html:
                return translated_html, {
                    "engine": "baidu",
                    "provider": "baidu",
                    "model": "baidu-general",
                    "format": "html",
                    "input_chars": input_chars,
                    "nodes": node_count,
                }
        text = _translation_text_content(html_content, text_content, subject)
        translated = _translate_text_with_chunks(text, lambda chunk: _call_baidu_translate(chunk, settings))
        return translated, {
            "engine": "baidu",
            "provider": "baidu",
            "model": "baidu-general",
            "format": "text",
            "input_chars": len(text),
        }
    if provider == "tencent":
        if html_content:
            def translator(text: str) -> str:
                return _call_tencent_translate(text, settings)
            translated_html, node_count, input_chars = _translate_html_text_nodes(
                html_content,
                translator,
                "tencent",
                lambda texts: _translate_texts_with_provider(texts, translator, "tencent"),
            )
            if translated_html:
                return translated_html, {
                    "engine": "tencent",
                    "provider": "tencent",
                    "model": "TextTranslate",
                    "format": "html",
                    "input_chars": input_chars,
                    "nodes": node_count,
                }
        text = _translation_text_content(html_content, text_content, subject)
        translated = _translate_text_with_chunks(text, lambda chunk: _call_tencent_translate(chunk, settings))
        return translated, {
            "engine": "tencent",
            "provider": "tencent",
            "model": "TextTranslate",
            "format": "text",
            "input_chars": len(text),
        }
    if provider == "google_cloud":
        if html_content:
            translated_html, node_count, input_chars = _translate_html_text_nodes(
                html_content,
                lambda text: _call_google_cloud_translate(text, settings),
                "google_cloud",
                lambda texts: _call_google_cloud_translate_batch(texts, settings),
            )
            if translated_html:
                return translated_html, {
                    "engine": "google_cloud",
                    "provider": "google_cloud",
                    "model": "cloud-translation-basic-v2",
                    "format": "html",
                    "input_chars": input_chars,
                    "nodes": node_count,
                }
        text = _translation_text_content(html_content, text_content, subject)
        translated = _translate_text_with_chunks(text, lambda chunk: _call_google_cloud_translate(chunk, settings))
        return translated, {
            "engine": "google_cloud",
            "provider": "google_cloud",
            "model": "cloud-translation-basic-v2",
            "format": "text",
            "input_chars": len(text),
        }
    wants_html = bool(html_content)
    content = _strip_layout_html_for_translation(html_content) if html_content else _normalize_translation_source_text(text_content)
    if subject:
        content = f"邮件主题：{subject}\n\n{content}"
    if not content.strip():
        raise RuntimeError("没有可翻译的邮件内容")
    translated, meta = _call_ai_translation_engine(content, wants_html)
    meta["input_chars"] = len(content)
    return _normalize_ai_translation(translated, wants_html), meta


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


def _safe_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


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
    _publish_mobile_event(event)
    return event


def _publish_mobile_event(event: dict):
    with _MOBILE_EVENT_LOCK:
        subscribers = list(_MOBILE_EVENT_SUBSCRIBERS)
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(event)
        except Exception:
            pass


def _mail_state_fingerprint() -> str:
    parts = []
    try:
        resp = http_session.get(urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", "api/accounts"), timeout=8)
        if resp.status_code == 200:
            payload = resp.json()
            accounts = payload if isinstance(payload, list) else payload.get("accounts", [])
            if isinstance(accounts, list):
                for item in accounts:
                    if not isinstance(item, dict):
                        continue
                    sync = item.get("syncStatus") if isinstance(item.get("syncStatus"), dict) else {}
                    parts.append(
                        "external:{id}:{messages}:{unseen}:{synced}".format(
                            id=item.get("id", ""),
                            messages=sync.get("messages", 0),
                            unseen=sync.get("unseen", 0),
                            synced=sync.get("syncedAt", ""),
                        )
                    )
    except Exception as exc:
        app.logger.debug("移动端邮件事件检测读取外部账号失败: %s", exc)

    settings = _read_viewer_settings()
    for mailbox in settings.get("mailboxes", []) if isinstance(settings.get("mailboxes"), list) else []:
        address = str(mailbox.get("address") or "").strip().lower()
        if address:
            parts.append(f"local:{address}:{mailbox.get('updated_at', '')}:{mailbox.get('created_at', '')}")

    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()


def _mobile_event_monitor_loop():
    global _MOBILE_EVENT_LAST_SEQ, _MOBILE_EVENT_LAST_FINGERPRINT
    while True:
        try:
            fingerprint = _mail_state_fingerprint()
            if fingerprint and fingerprint != _MOBILE_EVENT_LAST_FINGERPRINT:
                _MOBILE_EVENT_LAST_FINGERPRINT = fingerprint
                settings = _read_viewer_settings()
                event = _touch_sync_event(settings, "mail.changed", {"source": "mail-state-monitor"})
                _MOBILE_EVENT_LAST_SEQ = _safe_int(event.get("seq"), _MOBILE_EVENT_LAST_SEQ)
                _write_viewer_settings(settings)
        except Exception as exc:
            app.logger.warning("移动端邮件事件检测失败: %s", exc)
        time.sleep(max(5, MOBILE_PUSH_POLL_SECONDS))


def _ensure_mobile_event_monitor():
    global _MOBILE_EVENT_MONITOR_STARTED
    with _MOBILE_EVENT_LOCK:
        if _MOBILE_EVENT_MONITOR_STARTED:
            return
        _MOBILE_EVENT_MONITOR_STARTED = True
    threading.Thread(target=_mobile_event_monitor_loop, name="mobile-event-monitor", daemon=True).start()


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
    return jsonify({
        "success": False,
        "message": "需要二次确认",
        "require_confirmation": True,
        "action": action,
        "totp_enabled": _totp_enabled(),
    }), 403


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
        "cc": item.get("cc", ""),
        "bcc": item.get("bcc", ""),
        "subject": item.get("subject", ""),
        "text": item.get("text", ""),
        "html": item.get("html", ""),
        "attachments": item.get("attachments", []) if isinstance(item.get("attachments"), list) else [],
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
        "cc": item.get("cc", ""),
        "bcc": item.get("bcc", ""),
        "subject": item.get("subject", ""),
        "text": item.get("text", ""),
        "html": item.get("html", ""),
        "attachments": item.get("attachments", []) if isinstance(item.get("attachments"), list) else [],
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
        "cc": data.get("cc", ""),
        "bcc": data.get("bcc", ""),
        "subject": data.get("subject", ""),
        "text": data.get("text", ""),
        "html": data.get("html", ""),
        "attachments": _normalize_send_attachments(data.get("attachments", []), keep_content=True),
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
    account_type = str(account_type or "")
    account_id = str(account_id or "")
    folder = str(folder or "")
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
    scope_type = str(item.get("scope_type") or item.get("scopeType") or "all").strip().lower()
    if scope_type not in {"all", "group", "accounts"}:
        scope_type = "all"
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        fields = []
    normalized_fields = [
        field for field in fields
        if field in {"subject", "from", "to", "intro", "body"}
    ] or ["subject", "from", "intro"]
    match_mode = str(item.get("match_mode") or item.get("matchMode") or "any").strip().lower()
    if match_mode not in {"any", "all"}:
        match_mode = "any"
    raw_scope_accounts = item.get("scope_accounts", item.get("scopeAccounts", []))
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
        "scope_group": _normalize_account_group(item.get("scope_group") or item.get("scopeGroup") or ""),
        "scope_accounts": [
            str(account).strip()
            for account in raw_scope_accounts
            if str(account).strip()
        ][:100],
        "keywords": keywords[:30],
        "match_mode": match_mode,
        "fields": normalized_fields,
        "enabled": bool(enabled),
        "created_at": item.get("created_at") or item.get("createdAt") or _iso_now(),
        "updated_at": item.get("updated_at") or item.get("updatedAt") or _iso_now(),
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


def _portable_keyword_rule(item: dict) -> dict:
    normalized = _normalize_keyword_rule(item) or {}
    return {
        "id": normalized.get("id", ""),
        "name": normalized.get("name", ""),
        "scopeType": normalized.get("scope_type", "all"),
        "scopeGroup": normalized.get("scope_group", ""),
        "scopeAccounts": normalized.get("scope_accounts", []),
        "keywords": normalized.get("keywords", []),
        "matchMode": normalized.get("match_mode", "any"),
        "fields": normalized.get("fields", ["subject", "from", "intro"]),
        "enabled": bool(normalized.get("enabled", True)),
        "createdAt": normalized.get("created_at", ""),
        "updatedAt": normalized.get("updated_at", ""),
    }


def _coerce_string_list(value, limit: int = 100) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[\n,，]+", value)
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result[:limit]


def _coerce_regex_list(value, fallback: str = "", limit: int = 20) -> list[str]:
    items = _coerce_string_list(value, limit)
    if not items and fallback:
        items = [fallback]
    cleaned = []
    for regex in items:
        if regex and regex not in cleaned:
            cleaned.append(regex[:500])
    return cleaned[:limit]


def _bool_from_setting(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


AOSOM_ORDER_REGEXES = [
    r"Your\s+Order\s+#\s*([A-Z0-9-]+)",
    r"\bOrder\s*#\s*([A-Z0-9-]+)\b",
    r"\bOrder\s*Date\s*[\r\n\t ]+([A-Z0-9][A-Z0-9-]{5,})\b",
    r"\bOrder\s*(?:Number|No\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{5,})\b",
]
AOSOM_TRACKING_REGEXES = [
    r"\b(1Z[0-9A-Z]{16})\b",
    r"Tracking\s*(?:Number|#)?(?:\s+(?:UPS|FedEx|OnTrac|USPS|DHL)){0,5}\s+([A-Z0-9][A-Z0-9 -]{7,34}[A-Z0-9])",
    r"(?:UPS|FedEx|OnTrac|USPS|DHL)\s+([A-Z0-9][A-Z0-9 -]{7,34}[A-Z0-9])",
    r"\b(9[2345]\d{18,24})\b",
    r"\b(\d{12,22})\b",
]
AOSOM_CARRIER_REGEX = r"\b(UPS|FedEx|Federal Express|OnTrac|USPS|DHL|Canada Post|Purolator)\b"
AOSOM_SENDER_CONTAINS = "noreply@aosom.ca,noreply@aosom.com"
AOSOM_BODY_KEYWORDS = ["Tracking Number", "shipped"]


def _split_rule_terms(value) -> list[str]:
    return [item.strip().lower() for item in re.split(r"[,;\n]+", str(value or "")) if item.strip()]


def _aosom_shipped_defaults(sender_contains: str = AOSOM_SENDER_CONTAINS) -> dict:
    sender = (sender_contains or AOSOM_SENDER_CONTAINS).strip().lower()
    return {
        "name": "Aosom CA/US 发货物流提取",
        "sender_contains": sender,
        "subject_contains": "",
        "body_keywords": AOSOM_BODY_KEYWORDS,
        "keyword_match_mode": "any",
        "order_regex": AOSOM_ORDER_REGEXES[0],
        "order_regexes": AOSOM_ORDER_REGEXES,
        "tracking_regex": AOSOM_TRACKING_REGEXES[0],
        "tracking_regexes": AOSOM_TRACKING_REGEXES,
        "carrier_regex": AOSOM_CARRIER_REGEX,
    }


def _normalize_extraction_rule(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    template = str(item.get("template") or "custom").strip().lower()
    if template not in {"custom", "aosom_shipped"}:
        template = "custom"

    defaults = {}
    if template == "aosom_shipped":
        defaults = _aosom_shipped_defaults(str(item.get("sender_contains") or item.get("senderContains") or AOSOM_SENDER_CONTAINS))

    name = str(item.get("name") or defaults.get("name") or "").strip()
    sender_contains = str(item.get("sender_contains") or item.get("senderContains") or defaults.get("sender_contains") or "").strip()
    subject_contains = str(item.get("subject_contains") or item.get("subjectContains") or defaults.get("subject_contains") or "").strip()
    subject_exact = str(item.get("subject_exact") or item.get("subjectExact") or "").strip()
    provided_order_regex = str(item.get("order_regex") or item.get("orderRegex") or "").strip()
    provided_tracking_regex = str(item.get("tracking_regex") or item.get("trackingRegex") or "").strip()
    order_regex = provided_order_regex or str(defaults.get("order_regex") or "").strip()
    tracking_regex = provided_tracking_regex or str(defaults.get("tracking_regex") or "").strip()
    carrier_regex = str(item.get("carrier_regex") or item.get("carrierRegex") or defaults.get("carrier_regex") or "").strip()
    keywords = _coerce_string_list(item.get("keywords", defaults.get("keywords", [])), 30)
    body_keywords = _coerce_string_list(item.get("body_keywords", item.get("bodyKeywords", defaults.get("body_keywords", []))), 30)
    if template == "aosom_shipped":
        sender_terms = _split_rule_terms(sender_contains)
        for sender in _split_rule_terms(AOSOM_SENDER_CONTAINS):
            if sender not in sender_terms:
                sender_terms.append(sender)
        sender_contains = ",".join(sender_terms)
        body_keyword_keys = {keyword.lower() for keyword in body_keywords}
        for keyword in AOSOM_BODY_KEYWORDS:
            if keyword.lower() not in body_keyword_keys:
                body_keywords.append(keyword)
                body_keyword_keys.add(keyword.lower())
        if subject_contains == "Aosom Business: Your Aosom order has been shipped":
            subject_contains = ""
    raw_order_regexes = item.get("order_regexes", item.get("orderRegexes"))
    raw_tracking_regexes = item.get("tracking_regexes", item.get("trackingRegexes"))
    order_regexes = _coerce_regex_list(
        raw_order_regexes if raw_order_regexes is not None else defaults.get("order_regexes", []),
        provided_order_regex or defaults.get("order_regex", ""),
        20,
    )
    tracking_regexes = _coerce_regex_list(
        raw_tracking_regexes if raw_tracking_regexes is not None else defaults.get("tracking_regexes", []),
        provided_tracking_regex or defaults.get("tracking_regex", ""),
        20,
    )

    if not name or not tracking_regexes:
        return None
    if not any([sender_contains, subject_contains, subject_exact, keywords, body_keywords]):
        return None

    raw_match_mode = str(item.get("keyword_match_mode") or item.get("keywordMatchMode") or "any").strip().lower()
    keyword_match_mode = raw_match_mode if raw_match_mode in {"any", "all"} else "any"
    if template == "aosom_shipped":
        keyword_match_mode = "any"
    scan_limit = min(1000, max(20, _safe_int(item.get("scan_limit") or item.get("scanLimit"), EXTRACTION_SCAN_LIMIT)))
    now = _iso_now()
    return {
        "id": str(item.get("id") or _new_id("ext_")),
        "name": name[:100],
        "template": template,
        "enabled": _bool_from_setting(item.get("enabled"), True),
        "account_ids": _coerce_string_list(item.get("account_ids", item.get("accountIds", [])), 200),
        "account_emails": [
            _normalize_mailbox_address(email)
            for email in _coerce_string_list(item.get("account_emails", item.get("accountEmails", [])), 200)
            if _normalize_mailbox_address(email)
        ],
        "sender_contains": sender_contains[:240],
        "subject_contains": subject_contains[:240],
        "subject_exact": subject_exact[:240],
        "keywords": keywords,
        "body_keywords": body_keywords,
        "keyword_match_mode": keyword_match_mode,
        "order_regex": (order_regexes[0] if order_regexes else "")[:500],
        "order_regexes": order_regexes,
        "tracking_regex": (tracking_regexes[0] if tracking_regexes else "")[:500],
        "tracking_regexes": tracking_regexes,
        "carrier_regex": carrier_regex[:500],
        "scan_limit": scan_limit,
        "scan_state": item.get("scan_state") if isinstance(item.get("scan_state"), dict) else item.get("scanState") if isinstance(item.get("scanState"), dict) else {},
        "created_at": item.get("created_at") or item.get("createdAt") or now,
        "updated_at": item.get("updated_at") or item.get("updatedAt") or now,
        "last_scan_at": item.get("last_scan_at") or item.get("lastScanAt") or "",
        "last_scan_count": _safe_int(item.get("last_scan_count") or item.get("lastScanCount"), 0),
        "last_scan_error": str(item.get("last_scan_error") or item.get("lastScanError") or "")[:500],
    }


def _public_extraction_rule(item: dict) -> dict:
    normalized = _normalize_extraction_rule(item) or {}
    return {
        "id": normalized.get("id", ""),
        "name": normalized.get("name", ""),
        "template": normalized.get("template", "custom"),
        "enabled": bool(normalized.get("enabled", True)),
        "account_ids": normalized.get("account_ids", []),
        "account_emails": normalized.get("account_emails", []),
        "sender_contains": normalized.get("sender_contains", ""),
        "subject_contains": normalized.get("subject_contains", ""),
        "subject_exact": normalized.get("subject_exact", ""),
        "keywords": normalized.get("keywords", []),
        "body_keywords": normalized.get("body_keywords", []),
        "keyword_match_mode": normalized.get("keyword_match_mode", "any"),
        "order_regex": normalized.get("order_regex", ""),
        "order_regexes": normalized.get("order_regexes", []),
        "tracking_regex": normalized.get("tracking_regex", ""),
        "tracking_regexes": normalized.get("tracking_regexes", []),
        "carrier_regex": normalized.get("carrier_regex", ""),
        "scan_limit": normalized.get("scan_limit", EXTRACTION_SCAN_LIMIT),
        "scan_state": normalized.get("scan_state", {}),
        "created_at": normalized.get("created_at", ""),
        "updated_at": normalized.get("updated_at", ""),
        "last_scan_at": normalized.get("last_scan_at", ""),
        "last_scan_count": normalized.get("last_scan_count", 0),
        "last_scan_error": normalized.get("last_scan_error", ""),
    }


def _normalize_message_party(value) -> str:
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        address = str(value.get("address") or "").strip()
        return f"{name} <{address}>".strip() if name else address
    if isinstance(value, list):
        return ", ".join(_normalize_message_party(item) for item in value if item)
    return str(value or "").strip()


def _external_accounts_for_extraction() -> list[dict]:
    resp = http_session.get(urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", "api/accounts"), timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(_response_error(resp, f"外部账号读取失败 HTTP {resp.status_code}"))
    payload = resp.json()
    accounts = payload if isinstance(payload, list) else payload.get("accounts", [])
    return [item for item in accounts if isinstance(item, dict)]


def _account_matches_extraction_rule(rule: dict, account: dict) -> bool:
    account_ids = {str(item) for item in rule.get("account_ids", [])}
    account_emails = {str(item).lower() for item in rule.get("account_emails", [])}
    account_id = str(account.get("id") or "")
    account_email = _normalize_mailbox_address(account.get("email", ""))
    if account_ids and account_id not in account_ids:
        return False
    if account_emails and account_email.lower() not in account_emails:
        return False
    return True


def _mail_envelope_matches_extraction_rule(rule: dict, mail: dict, include_keywords: bool = True) -> bool:
    subject = str(mail.get("subject") or "").strip()
    sender = _normalize_message_party(mail.get("from"))
    haystack = f"{sender}\n{subject}".lower()
    sender_terms = _split_rule_terms(rule.get("sender_contains"))
    subject_terms = _split_rule_terms(rule.get("subject_contains"))
    subject_exact = str(rule.get("subject_exact") or "").lower()
    if sender_terms and not any(term in sender.lower() for term in sender_terms):
        return False
    if subject_exact and subject.lower() != subject_exact:
        return False
    if subject_terms and not any(term in subject.lower() for term in subject_terms):
        return False
    keywords = [str(item).lower() for item in rule.get("keywords", []) if str(item).strip()]
    if not include_keywords:
        return True
    if keywords:
        matched = [keyword in haystack for keyword in keywords]
        if rule.get("keyword_match_mode") == "all" and not all(matched):
            return False
        if rule.get("keyword_match_mode") != "all" and not any(matched):
            return False
    return True


def _keywords_match(rule: dict, keywords: list[str], text: str) -> bool:
    if not keywords:
        return True
    haystack = (text or "").lower()
    matched = [str(keyword).lower() in haystack for keyword in keywords if str(keyword).strip()]
    if not matched:
        return True
    if rule.get("keyword_match_mode") == "all":
        return all(matched)
    return any(matched)


def _message_detail_text(detail: dict, mail: dict | None = None) -> str:
    mail = mail or {}
    parts = [
        str(detail.get("subject") or mail.get("subject") or ""),
        _normalize_message_party(detail.get("from") or mail.get("from")),
        str(detail.get("text") or ""),
        _extract_plain_text(str(detail.get("html") or "")),
    ]
    return "\n".join(part for part in parts if part).strip()


def _mail_detail_matches_extraction_rule(rule: dict, detail: dict, mail: dict) -> bool:
    if not _mail_envelope_matches_extraction_rule(rule, {
        **mail,
        "subject": detail.get("subject") or mail.get("subject"),
        "from": detail.get("from") or mail.get("from"),
    }, include_keywords=False):
        return False
    keywords = [str(item).lower() for item in rule.get("keywords", []) if str(item).strip()]
    body_keywords = [str(item).lower() for item in rule.get("body_keywords", []) if str(item).strip()]
    if not keywords and not body_keywords:
        return True
    text = _message_detail_text(detail, mail).lower()
    return _keywords_match(rule, keywords, text) and _keywords_match(rule, body_keywords, text)


def _find_unique_regex(regex: str, text: str, flags=re.IGNORECASE) -> list[str]:
    if not regex:
        return []
    result = []
    try:
        matches = re.finditer(regex, text or "", flags)
    except re.error as exc:
        raise RuntimeError(f"正则表达式错误: {exc}") from exc
    for match in matches:
        value = match.group(1) if match.groups() else match.group(0)
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _find_order_matches(regexes: list[str], text: str, validate: bool = False) -> list[dict]:
    matches = []
    seen = set()
    for regex in regexes:
        if not regex:
            continue
        try:
            iterator = re.finditer(regex, text or "", re.IGNORECASE)
        except re.error as exc:
            raise RuntimeError(f"订单号正则表达式错误: {exc}") from exc
        for match in iterator:
            value = match.group(1) if match.groups() else match.group(0)
            value = str(value or "").strip()
            if not value:
                continue
            if validate and not _valid_order_number(value):
                continue
            key = value.upper()
            if key in seen:
                continue
            seen.add(key)
            matches.append({
                "order_number": value,
                "start": match.start(),
                "end": match.end(),
            })
    matches.sort(key=lambda item: item["start"])
    return matches


def _valid_order_number(value: str) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if len(compact) < 8 or len(compact) > 32:
        return False
    if not re.fullmatch(r"[A-Z0-9]+", compact):
        return False
    if compact in {"SHIPPED", "TRACKING", "NUMBER", "ORDERDATE", "SHIPPING", "PAYMENT", "METHOD"}:
        return False
    return bool(re.search(r"\d", compact))


def _slice_order_contexts(text: str, order_matches: list[dict]) -> list[dict]:
    if not order_matches:
        return [{"order_number": "", "text": text or ""}]
    if len(order_matches) == 1:
        return [{"order_number": order_matches[0]["order_number"], "text": text or ""}]
    contexts = []
    for index, match in enumerate(order_matches):
        next_start = order_matches[index + 1]["start"] if index + 1 < len(order_matches) else len(text or "")
        start = max(0, match["start"])
        end = min(len(text or ""), max(match["end"], next_start))
        contexts.append({
            "order_number": match["order_number"],
            "text": (text or "")[start:end],
        })
    return contexts


def _shipment_carrier_from_context(context: str, carrier_regex: str) -> str:
    if not carrier_regex:
        return ""
    try:
        matches = list(re.finditer(carrier_regex, context or "", re.IGNORECASE))
    except re.error:
        return ""
    return matches[-1].group(1 if matches[-1].groups() else 0).strip() if matches else ""


def _normalize_tracking_number(value: str) -> str:
    return re.sub(r"[\s-]+", "", str(value or "").strip().upper())


def _valid_tracking_number(tracking: str, context: str, prefix: str = "") -> bool:
    compact = _normalize_tracking_number(tracking)
    if len(compact) < 8 or len(compact) > 35:
        return False
    if not re.fullmatch(r"[A-Z0-9]+", compact):
        return False
    lower_prefix = (prefix or "").lower()
    lower_context = (context or "").lower()
    if compact.startswith("1Z") and len(compact) == 18:
        return True
    if compact.startswith(("92", "93", "94", "95")) and len(compact) >= 20:
        return True
    if compact.isdigit():
        if re.search(r"\b(call|phone|tel|fax|amount|date|order)\b.{0,24}$", lower_prefix, re.IGNORECASE):
            return False
        return bool(re.search(r"\b(tracking|fedex|federal express|ontrac|usps|ups|dhl)\b", lower_prefix[-100:], re.IGNORECASE))
    if re.search(r"\b(ups|fedex|federal express|ontrac|usps|dhl|tracking)\b", lower_context, re.IGNORECASE):
        return True
    return not compact.isdigit()


def _extract_shipments_from_text(text: str, tracking_regex: str, carrier_regex: str) -> list[dict]:
    shipments = []
    seen = set()
    try:
        matches = list(re.finditer(tracking_regex, text or "", re.IGNORECASE))
    except re.error as exc:
        raise RuntimeError(f"物流单号正则表达式错误: {exc}") from exc
    for match in matches:
        tracking = match.group(1) if match.groups() else match.group(0)
        context_start = max(0, match.start() - 160)
        context_end = min(len(text), match.end() + 120)
        context = text[context_start:context_end]
        prefix = context[:match.start() - context_start]
        tracking = _normalize_tracking_number(tracking)
        if not tracking or tracking in seen or not _valid_tracking_number(tracking, context, prefix):
            continue
        seen.add(tracking)
        item_ref_match = re.match(r"\s*\(([^)]{1,120})\)", text[match.end():])
        carrier = _shipment_carrier_from_context(context[:match.start() - context_start], carrier_regex) or _shipment_carrier_from_context(context, carrier_regex)
        if carrier.lower() == "federal express":
            carrier = "FedEx"
        shipments.append({
            "carrier": carrier,
            "tracking_number": tracking,
            "item_ref": item_ref_match.group(1).strip() if item_ref_match else "",
            "context": re.sub(r"\s+", " ", context).strip()[:500],
        })
    return shipments


def _extract_shipments_from_patterns(text: str, tracking_regexes: list[str], carrier_regex: str) -> list[dict]:
    shipments = []
    seen = set()
    for regex in tracking_regexes:
        for shipment in _extract_shipments_from_text(text, regex, carrier_regex):
            tracking = shipment.get("tracking_number", "")
            if tracking and tracking not in seen:
                seen.add(tracking)
                shipments.append(shipment)
    return shipments


def _extraction_record_id(rule: dict, account: dict, mail: dict, order_number: str, shipments: list[dict]) -> str:
    tracking_part = ",".join(sorted({str(item.get("tracking_number") or "").strip().upper() for item in shipments if item.get("tracking_number")}))
    raw = "|".join([
        str(rule.get("id") or ""),
        str(rule.get("template") or "custom"),
        _normalize_mailbox_address(account.get("email", "")) or str(account.get("id") or ""),
        str(order_number or ""),
        tracking_part,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _extraction_result_identity(item: dict) -> str:
    public = _public_extraction_result(item)
    message = public.get("message") if isinstance(public.get("message"), dict) else {}
    tracking_part = ",".join(sorted({
        str(shipment.get("tracking_number") or "").strip().upper()
        for shipment in public.get("shipments", [])
        if shipment.get("tracking_number")
    }))
    raw = "|".join([
        str(public.get("rule_id") or ""),
        str(public.get("template") or "custom"),
        _normalize_mailbox_address(message.get("account_email") or "") or str(message.get("account_id") or ""),
        str(public.get("order_number") or ""),
        tracking_part,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32] if raw.strip("|") else public.get("id", "")


def _extract_records_from_message(rule: dict, account: dict, mail: dict, detail: dict) -> list[dict]:
    text = _message_detail_text(detail, mail)
    order_regexes = rule.get("order_regexes", []) or ([rule.get("order_regex", "")] if rule.get("order_regex") else [])
    tracking_regexes = rule.get("tracking_regexes", []) or ([rule.get("tracking_regex", "")] if rule.get("tracking_regex") else [])
    validate_orders = str(rule.get("template") or "").strip().lower() == "aosom_shipped"
    order_contexts = _slice_order_contexts(text, _find_order_matches(order_regexes, text, validate=validate_orders))
    message = {
        "account_id": str(account.get("id") or ""),
        "account_email": _normalize_mailbox_address(account.get("email", "")),
        "account_name": str(account.get("displayName") or account.get("name") or ""),
        "uid": str(mail.get("uid") or mail.get("id") or ""),
        "folder": str(mail.get("folder") or "INBOX"),
        "subject": str(detail.get("subject") or mail.get("subject") or ""),
        "from": _normalize_message_party(detail.get("from") or mail.get("from")),
        "date": str(detail.get("date") or mail.get("date") or ""),
    }
    records = []
    for order_context in order_contexts:
        order_number = order_context["order_number"]
        shipments = _extract_shipments_from_patterns(
            order_context["text"],
            tracking_regexes,
            rule.get("carrier_regex", ""),
        )
        if not shipments:
            continue
        record = {
            "rule_id": rule.get("id", ""),
            "rule_name": rule.get("name", ""),
            "template": rule.get("template", "custom"),
            "order_number": order_number,
            "shipments": shipments,
            "message": message,
            "extracted_at": _iso_now(),
        }
        record["id"] = _extraction_record_id(rule, account, mail, order_number, shipments)
        records.append(record)
    return records


def _public_extraction_result(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    shipments = []
    for shipment in item.get("shipments", []) if isinstance(item.get("shipments"), list) else []:
        if not isinstance(shipment, dict):
            continue
        tracking_number = str(shipment.get("tracking_number") or "").strip()
        if not tracking_number:
            continue
        shipments.append({
            "carrier": str(shipment.get("carrier") or "").strip(),
            "tracking_number": tracking_number,
            "item_ref": str(shipment.get("item_ref") or "").strip(),
        })
    return {
        "id": str(item.get("id") or ""),
        "rule_id": str(item.get("rule_id") or ""),
        "rule_name": str(item.get("rule_name") or ""),
        "template": str(item.get("template") or "custom"),
        "order_number": str(item.get("order_number") or ""),
        "shipments": shipments,
        "message": item.get("message") if isinstance(item.get("message"), dict) else {},
        "extracted_at": str(item.get("extracted_at") or ""),
        "updated_at": str(item.get("updated_at") or item.get("extracted_at") or ""),
    }


def _extraction_result_document(item: dict) -> dict:
    public = _public_extraction_result(item)
    if not public.get("id"):
        return {}
    identity = _extraction_result_identity(public)
    if identity:
        public["id"] = identity
    message = public.get("message") if isinstance(public.get("message"), dict) else {}
    message_date = str(message.get("date") or "")
    parsed_date = _safe_iso_datetime(message_date)
    tracking_numbers = [
        shipment.get("tracking_number")
        for shipment in public.get("shipments", [])
        if shipment.get("tracking_number")
    ]
    doc = {
        **public,
        "natural_key": public.get("id", ""),
        "tracking_numbers": tracking_numbers,
        "account_id": str(message.get("account_id") or ""),
        "account_email": _normalize_mailbox_address(message.get("account_email") or ""),
        "folder": str(message.get("folder") or ""),
        "uid": str(message.get("uid") or ""),
        "subject": str(message.get("subject") or ""),
        "from": str(message.get("from") or ""),
        "message_date": message_date,
        "message_timestamp": parsed_date.timestamp() if parsed_date else 0,
    }
    return doc


def _dedupe_public_extraction_results(items: list[dict]) -> list[dict]:
    by_key = {}
    for item in items or []:
        public = _public_extraction_result(item)
        key = _extraction_result_identity(public)
        if not key:
            key = public.get("id", "")
        if not key:
            continue
        public["id"] = key
        old = by_key.get(key)
        if not old:
            by_key[key] = public
            continue
        old_time = str(old.get("updated_at") or old.get("extracted_at") or "")
        new_time = str(public.get("updated_at") or public.get("extracted_at") or "")
        if new_time >= old_time:
            if old.get("extracted_at") and not public.get("extracted_at"):
                public["extracted_at"] = old["extracted_at"]
            by_key[key] = public
    return list(by_key.values())


def _extraction_result_time_key(item: dict) -> str:
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    return str(item.get("updated_at") or item.get("extracted_at") or message.get("date") or "")


def _aggregate_public_extraction_results_by_order(items: list[dict]) -> list[dict]:
    by_key = {}
    for item in items or []:
        public = _public_extraction_result(item)
        order_number = str(public.get("order_number") or "").strip()
        message = public.get("message") if isinstance(public.get("message"), dict) else {}
        account_key = _normalize_mailbox_address(message.get("account_email") or "") or str(message.get("account_id") or "")
        if order_number:
            raw_key = "|".join([
                str(public.get("rule_id") or "").strip(),
                str(public.get("template") or "custom").strip().lower(),
                account_key.lower(),
                order_number.lower(),
            ])
            key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
        else:
            key = _extraction_result_identity(public) or public.get("id", "")
        if not key:
            continue
        old = by_key.get(key)
        if not old:
            public["id"] = key
            public["merged_count"] = 1
            by_key[key] = public
            continue

        shipments = {}
        for source in [old, public]:
            for shipment in source.get("shipments", []) if isinstance(source.get("shipments"), list) else []:
                tracking = str(shipment.get("tracking_number") or "").strip().upper()
                if not tracking:
                    continue
                existing = shipments.get(tracking, {})
                shipments[tracking] = {
                    "carrier": existing.get("carrier") or str(shipment.get("carrier") or "").strip(),
                    "tracking_number": tracking,
                    "item_ref": existing.get("item_ref") or str(shipment.get("item_ref") or "").strip(),
                }

        if _extraction_result_time_key(public) >= _extraction_result_time_key(old):
            merged = public
            if old.get("extracted_at") and not merged.get("extracted_at"):
                merged["extracted_at"] = old["extracted_at"]
        else:
            merged = old
        merged["id"] = key
        merged["shipments"] = list(shipments.values())
        merged["merged_count"] = int(old.get("merged_count") or 1) + 1
        by_key[key] = merged
    return list(by_key.values())


def _extraction_results_collection():
    global _EXTRACTION_MONGO_CLIENT, _EXTRACTION_MONGO_READY
    if EXTRACTION_RESULT_STORE in {"settings", "file", "json"} or not MONGO_URL:
        return None
    if _EXTRACTION_MONGO_READY and _EXTRACTION_MONGO_CLIENT is not None:
        return _EXTRACTION_MONGO_CLIENT[MONGO_DB_NAME]["extraction_results"]
    with _EXTRACTION_MONGO_LOCK:
        if _EXTRACTION_MONGO_READY and _EXTRACTION_MONGO_CLIENT is not None:
            return _EXTRACTION_MONGO_CLIENT[MONGO_DB_NAME]["extraction_results"]
        try:
            _EXTRACTION_MONGO_CLIENT = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
            _EXTRACTION_MONGO_CLIENT.admin.command("ping")
            collection = _EXTRACTION_MONGO_CLIENT[MONGO_DB_NAME]["extraction_results"]
            collection.create_index([("id", ASCENDING)], unique=True, background=True)
            try:
                collection.create_index([("natural_key", ASCENDING)], unique=True, sparse=True, background=True)
            except Exception as index_exc:
                app.logger.warning("数据抽取 natural_key 唯一索引暂不可用，查询仍会去重: %s", index_exc)
            collection.create_index([("rule_id", ASCENDING), ("message_timestamp", DESCENDING), ("message_date", DESCENDING)], background=True)
            collection.create_index([("order_number", ASCENDING)], background=True)
            collection.create_index([("tracking_numbers", ASCENDING)], background=True)
            collection.create_index([("account_email", ASCENDING), ("message_timestamp", DESCENDING), ("message_date", DESCENDING)], background=True)
            collection.create_index([("updated_at", DESCENDING)], background=True)
            _EXTRACTION_MONGO_READY = True
            return collection
        except Exception as exc:
            _EXTRACTION_MONGO_READY = False
            app.logger.warning("数据抽取 Mongo 存储不可用，临时回退 settings 存储: %s", exc)
            return None


def _migrate_extraction_results_to_mongo(settings: dict | None = None):
    global _EXTRACTION_MIGRATION_DONE
    if _EXTRACTION_MIGRATION_DONE:
        return
    collection = _extraction_results_collection()
    if collection is None:
        return
    settings = settings or _read_viewer_settings()
    legacy_results = [
        _extraction_result_document(item)
        for item in _settings_list(settings, "extraction_results")
        if isinstance(item, dict) and item.get("id")
    ]
    legacy_results = [item for item in legacy_results if item.get("id")]
    if legacy_results:
        operations = [
            UpdateOne({"id": item["id"]}, {"$set": item, "$setOnInsert": {"created_at": item.get("extracted_at") or _iso_now()}}, upsert=True)
            for item in legacy_results
        ]
        try:
            collection.bulk_write(operations, ordered=False)
        except BulkWriteError as exc:
            app.logger.warning("历史抽取结果迁移存在重复键，已跳过重复项继续迁移: %s", exc.details)
        settings["extraction_results_migrated_at"] = _iso_now()
        settings["extraction_results_legacy_count"] = len(legacy_results)
        settings["extraction_results"] = []
        _write_viewer_settings(settings)
    _EXTRACTION_MIGRATION_DONE = True


def _merge_extraction_results_settings(settings: dict, records: list[dict]) -> list[dict]:
    existing = _dedupe_public_extraction_results([
        _public_extraction_result(item)
        for item in _settings_list(settings, "extraction_results")
        if isinstance(item, dict) and item.get("id")
    ])
    by_id = {item["id"]: item for item in existing if item.get("id")}
    now = _iso_now()
    for record in records:
        public = _public_extraction_result(record)
        if not public.get("id"):
            continue
        public["id"] = _extraction_result_identity(public) or public["id"]
        old = by_id.get(public["id"], {})
        public["updated_at"] = now
        if old.get("extracted_at"):
            public["extracted_at"] = old["extracted_at"]
        by_id[public["id"]] = public
    merged = sorted(
        by_id.values(),
        key=lambda item: (
            str((item.get("message") or {}).get("date") or ""),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )[:EXTRACTION_RESULT_LIMIT]
    settings["extraction_results"] = merged
    return merged


def _merge_extraction_results(settings: dict, records: list[dict]) -> list[dict]:
    collection = _extraction_results_collection()
    if collection is None:
        return _merge_extraction_results_settings(settings, records)
    _migrate_extraction_results_to_mongo(settings)
    now = _iso_now()
    docs = []
    for record in records:
        doc = _extraction_result_document(record)
        if not doc.get("id"):
            continue
        old = collection.find_one({"natural_key": doc["id"]}, {"extracted_at": 1})
        if not old:
            old = collection.find_one({"id": doc["id"]}, {"extracted_at": 1})
        doc["updated_at"] = now
        if old and old.get("extracted_at"):
            doc["extracted_at"] = old["extracted_at"]
        docs.append(doc)
    if not docs:
        return []
    operations = [
        UpdateOne({"natural_key": item["id"]}, {"$set": item, "$setOnInsert": {"created_at": item.get("extracted_at") or now}}, upsert=True)
        for item in docs
    ]
    collection.bulk_write(operations, ordered=False)
    _cleanup_duplicate_extraction_results(collection)
    return [_public_extraction_result(item) for item in docs]


def _cleanup_duplicate_extraction_results(collection=None, max_docs: int = 5000) -> int:
    collection = collection or _extraction_results_collection()
    if collection is None:
        return 0
    try:
        docs = list(collection.find({}, {"_id": 1, "id": 1, "natural_key": 1, "rule_id": 1, "template": 1, "message": 1, "order_number": 1, "shipments": 1, "updated_at": 1, "extracted_at": 1}).sort([("updated_at", DESCENDING)]).limit(max_docs))
        by_key = {}
        delete_ids = []
        set_ops = []
        for doc in docs:
            public = _public_extraction_result(doc)
            key = _extraction_result_identity(public) or doc.get("natural_key") or str(doc.get("id") or "")
            if not key:
                continue
            if key in by_key:
                delete_ids.append(doc["_id"])
                continue
            by_key[key] = doc["_id"]
            if not doc.get("natural_key") or doc.get("id") != key:
                set_ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"natural_key": key, "id": key}}))
        deleted_count = 0
        if delete_ids:
            result = collection.delete_many({"_id": {"$in": delete_ids}})
            deleted_count = int(result.deleted_count or 0)
        if set_ops:
            try:
                collection.bulk_write(set_ops, ordered=False)
            except (BulkWriteError, DuplicateKeyError) as exc:
                app.logger.warning("数据抽取重复结果键修正失败，已保留查询层去重: %s", exc)
        if deleted_count:
            return deleted_count
    except Exception as exc:
        app.logger.warning("数据抽取重复结果清理失败: %s", exc)
    return 0


def _extraction_result_matches_rule_scope(item: dict, rule: dict | None = None, rule_id: str = "") -> bool:
    if not isinstance(item, dict):
        return False
    public = _public_extraction_result(item)
    item_rule_id = str(public.get("rule_id") or item.get("rule_id") or "")
    return bool(rule_id and item_rule_id == str(rule_id))


def _delete_extraction_results(rule_id: str = "", rule: dict | None = None) -> int:
    collection = _extraction_results_collection()
    if collection is None:
        return 0
    if not rule_id and not rule:
        result = collection.delete_many({})
        return int(result.deleted_count or 0)
    if rule_id:
        result = collection.delete_many({"rule_id": rule_id})
        return int(result.deleted_count or 0)
    return 0


def _query_extraction_results(
    rule_id: str = "",
    order_number: str = "",
    tracking_number: str = "",
    account_email: str = "",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int, str]:
    collection = _extraction_results_collection()
    if collection is None:
        settings = _read_viewer_settings()
        results = [
            _public_extraction_result(item)
            for item in _settings_list(settings, "extraction_results")
            if isinstance(item, dict) and item.get("id")
        ]
        results = _dedupe_public_extraction_results(results)
        if rule_id:
            results = [item for item in results if str(item.get("rule_id") or "") == rule_id]
        if order_number:
            results = [item for item in results if str(item.get("order_number") or "").lower() == order_number.lower()]
        if tracking_number:
            results = [
                item for item in results
                if any(str(ship.get("tracking_number") or "").lower() == tracking_number.lower() for ship in item.get("shipments", []))
            ]
        if account_email:
            results = [
                item for item in results
                if str((item.get("message") or {}).get("account_email") or "").lower() == account_email.lower()
            ]
        results = _aggregate_public_extraction_results_by_order(results)
        return results[offset:offset + limit], len(results), "settings"
    try:
        _migrate_extraction_results_to_mongo()
    except Exception as exc:
        app.logger.warning("数据抽取结果迁移失败，查询将继续读取现有集合: %s", exc, exc_info=True)
    query = {}
    if rule_id:
        query["rule_id"] = rule_id
    if order_number:
        query["order_number"] = {"$regex": f"^{re.escape(order_number)}$", "$options": "i"}
    if tracking_number:
        query["tracking_numbers"] = {"$regex": f"^{re.escape(tracking_number)}$", "$options": "i"}
    if account_email:
        query["account_email"] = {"$regex": f"^{re.escape(account_email)}$", "$options": "i"}
    cursor = collection.find(query, {"_id": 0}).sort([
        ("message_timestamp", DESCENDING),
        ("message_date", DESCENDING),
        ("updated_at", DESCENDING),
    ]).limit(min(5000, max(limit + offset, limit * 5)))
    results = _dedupe_public_extraction_results([_public_extraction_result(item) for item in cursor])
    results = _aggregate_public_extraction_results_by_order(results)
    total = len(results)
    return results[offset:offset + limit], total, "mongo"


def _fetch_external_mail_list(account_id: str, limit: int) -> list[dict]:
    resp = http_session.get(
        urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", f"api/accounts/{quote(str(account_id), safe='')}/mails"),
        params={"folder": "__memail_all__", "count": limit, "offset": 0, "cacheOnly": 1},
        timeout=25,
    )
    if resp.status_code != 200:
        raise RuntimeError(_response_error(resp, f"邮件列表读取失败 HTTP {resp.status_code}"))
    payload = resp.json()
    return payload.get("mails", []) if isinstance(payload, dict) else []


def _fetch_external_mail_page(account_id: str, count: int, offset: int) -> list[dict]:
    resp = http_session.get(
        urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", f"api/accounts/{quote(str(account_id), safe='')}/mails"),
        params={"folder": "__memail_all__", "count": count, "offset": offset, "cacheOnly": 1},
        timeout=25,
    )
    if resp.status_code != 200:
        raise RuntimeError(_response_error(resp, f"邮件列表读取失败 HTTP {resp.status_code}"))
    payload = resp.json()
    return payload.get("mails", []) if isinstance(payload, dict) else []


def _fetch_external_mail_detail(account_id: str, mail: dict) -> dict:
    uid = str(mail.get("uid") or mail.get("id") or "")
    if not uid:
        return {}
    resp = http_session.get(
        urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", f"api/accounts/{quote(str(account_id), safe='')}/mails/{quote(uid, safe='')}"),
        params={"folder": str(mail.get("folder") or "INBOX"), "markSeen": "0"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(_response_error(resp, f"邮件详情读取失败 HTTP {resp.status_code}"))
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def _mail_uid_number(mail: dict) -> int:
    return max(0, _safe_int(mail.get("uid") or mail.get("id"), 0))


def _extraction_scope_key(account_id: str, folder: str) -> str:
    raw = f"{account_id}:{folder or 'INBOX'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _extraction_candidate_mails(rule: dict, account_id: str) -> tuple[list[dict], int]:
    scan_state = rule.get("scan_state") if isinstance(rule.get("scan_state"), dict) else {}
    by_key = {}
    checked = 0
    offset = 0
    max_pages = max(1, (rule.get("scan_limit", EXTRACTION_SCAN_LIMIT) + EXTRACTION_SCAN_PAGE_SIZE - 1) // EXTRACTION_SCAN_PAGE_SIZE)
    for _ in range(max_pages):
        page = _fetch_external_mail_page(account_id, EXTRACTION_SCAN_PAGE_SIZE, offset)
        if not page:
            break
        checked += len(page)
        for mail in page:
            if not isinstance(mail, dict):
                continue
            folder = str(mail.get("folder") or "INBOX")
            uid = _mail_uid_number(mail)
            scope_key = _extraction_scope_key(account_id, folder)
            state = scan_state.get(scope_key, {}) if isinstance(scan_state.get(scope_key), dict) else {}
            high_uid = _safe_int(state.get("highest_uid") or state.get("highestUid"), 0)
            recent = state.get("recent_uids") if isinstance(state.get("recent_uids"), list) else []
            if uid and uid <= high_uid and str(uid) not in {str(item) for item in recent}:
                continue
            dedupe_key = f"{folder}:{uid or mail.get('id') or offset}"
            by_key[dedupe_key] = mail
        if len(page) < EXTRACTION_SCAN_PAGE_SIZE:
            break
        offset += EXTRACTION_SCAN_PAGE_SIZE
    return list(by_key.values()), checked


def _update_extraction_scan_state(rule: dict, account_id: str, mails: list[dict]):
    scan_state = rule.get("scan_state") if isinstance(rule.get("scan_state"), dict) else {}
    for mail in mails:
        if not isinstance(mail, dict):
            continue
        uid = _mail_uid_number(mail)
        if not uid:
            continue
        folder = str(mail.get("folder") or "INBOX")
        scope_key = _extraction_scope_key(account_id, folder)
        state = scan_state.get(scope_key, {}) if isinstance(scan_state.get(scope_key), dict) else {}
        recent = [str(item) for item in state.get("recent_uids", []) if str(item)]
        uid_text = str(uid)
        if uid_text not in recent:
            recent.insert(0, uid_text)
        scan_state[scope_key] = {
            "account_id": str(account_id),
            "folder": folder,
            "highest_uid": max(_safe_int(state.get("highest_uid") or state.get("highestUid"), 0), uid),
            "recent_uids": recent[:EXTRACTION_LOOKBACK_PER_SCOPE],
            "updated_at": _iso_now(),
        }
    rule["scan_state"] = dict(list(scan_state.items())[-2000:])


def _scan_extraction_rule(rule: dict) -> tuple[list[dict], dict]:
    normalized = _normalize_extraction_rule(rule)
    if not normalized or not normalized.get("enabled", True):
        return [], {"checked": 0, "matched": 0, "records": 0, "failed": 0}
    records = []
    checked = 0
    matched = 0
    failed = 0
    for account in _external_accounts_for_extraction():
        if not _account_matches_extraction_rule(normalized, account):
            continue
        account_id = str(account.get("id") or "")
        if not account_id:
            continue
        try:
            mails, account_checked = _extraction_candidate_mails(normalized, account_id)
            checked += account_checked
        except Exception as exc:
            failed += 1
            app.logger.warning("邮件数据抽取读取列表失败: rule=%s account=%s error=%s", normalized.get("name"), account_id, exc)
            continue
        processed_mails = []
        for mail in mails:
            if not isinstance(mail, dict):
                continue
            processed_mails.append(mail)
            if not _mail_envelope_matches_extraction_rule(normalized, mail, include_keywords=False):
                continue
            try:
                detail = _fetch_external_mail_detail(account_id, mail)
                if not _mail_detail_matches_extraction_rule(normalized, detail, mail):
                    continue
                matched += 1
                records.extend(_extract_records_from_message(normalized, account, mail, detail))
            except Exception as exc:
                failed += 1
                app.logger.warning(
                    "邮件数据抽取读取详情失败: rule=%s account=%s uid=%s error=%s",
                    normalized.get("name"),
                    account_id,
                    mail.get("uid") or mail.get("id") or "",
                    exc,
                )
        _update_extraction_scan_state(normalized, account_id, processed_mails)
    if isinstance(rule, dict):
        rule["scan_state"] = normalized.get("scan_state", {})
    return records, {"checked": checked, "matched": matched, "records": len(records), "failed": failed}


def _run_extraction_scan_once(rule_id: str = "") -> dict:
    settings = _read_viewer_settings()
    rules = [
        _normalize_extraction_rule(item)
        for item in _settings_list(settings, "extraction_rules")
    ]
    rules = [item for item in rules if item and item.get("enabled", True)]
    if rule_id:
        rules = [item for item in rules if item.get("id") == rule_id]
    records = []
    summary = {"rules": 0, "checked": 0, "matched": 0, "records": 0, "failed": 0, "errors": []}
    for rule in rules:
        summary["rules"] += 1
        try:
            rule_records, stats = _scan_extraction_rule(rule)
            records.extend(rule_records)
            summary["checked"] += stats.get("checked", 0)
            summary["matched"] += stats.get("matched", 0)
            summary["records"] += stats.get("records", 0)
            summary["failed"] += stats.get("failed", 0)
            rule["last_scan_at"] = _iso_now()
            rule["last_scan_count"] = stats.get("records", 0)
            rule["last_scan_error"] = f"{stats.get('failed', 0)} 封邮件读取失败" if stats.get("failed", 0) else ""
        except Exception as exc:
            message = str(exc)
            rule["last_scan_at"] = _iso_now()
            rule["last_scan_count"] = 0
            rule["last_scan_error"] = message[:500]
            summary["errors"].append({"rule_id": rule.get("id", ""), "message": message})
            app.logger.warning("邮件数据抽取规则扫描失败: %s %s", rule.get("name"), exc, exc_info=True)
    if records:
        _merge_extraction_results(settings, records)
    all_rules = [
        _normalize_extraction_rule(item)
        for item in _settings_list(settings, "extraction_rules")
    ]
    by_id = {item.get("id"): item for item in all_rules if item}
    for rule in rules:
        if rule.get("id") in by_id:
            by_id[rule["id"]].update(rule)
    settings["extraction_rules"] = list(by_id.values())[-200:]
    if records or summary["errors"]:
        _touch_sync_event(settings, "extraction.results.updated", {
            "records": summary["records"],
            "errors": len(summary["errors"]),
        })
    _write_viewer_settings(settings)
    return {"summary": summary, "records": [_public_extraction_result(item) for item in records]}


def _extraction_scanner_loop():
    global _EXTRACTION_SCAN_RUNNING
    while True:
        time.sleep(max(30, EXTRACTION_SCAN_INTERVAL_SECONDS))
        with _EXTRACTION_SCAN_LOCK:
            if _EXTRACTION_SCAN_RUNNING:
                continue
            _EXTRACTION_SCAN_RUNNING = True
        try:
            _run_extraction_scan_once()
        except Exception as exc:
            app.logger.warning("邮件数据抽取后台扫描失败: %s", exc, exc_info=True)
        finally:
            with _EXTRACTION_SCAN_LOCK:
                _EXTRACTION_SCAN_RUNNING = False


def _ensure_extraction_scanner():
    global _EXTRACTION_SCAN_STARTED
    if EXTRACTION_SCAN_INTERVAL_SECONDS <= 0:
        return
    with _EXTRACTION_SCAN_LOCK:
        if _EXTRACTION_SCAN_STARTED:
            return
        _EXTRACTION_SCAN_STARTED = True
    threading.Thread(target=_extraction_scanner_loop, name="extraction-scanner", daemon=True).start()


def _portable_account_from_local_mailbox(item: dict) -> dict | None:
    address = _normalize_mailbox_address(item.get("address", ""))
    if not address:
        return None
    return {
        "id": address,
        "type": "local",
        "address": address,
        "displayName": _normalize_display_name(item.get("display_name", ""), address),
        "sendName": _normalize_display_name(item.get("send_name", ""), ""),
        "group": _normalize_account_group(item.get("group", "")),
        "provider": "local",
        "host": "",
        "port": 993,
        "secure": True,
        "smtpHost": "",
        "smtpPort": 465,
        "smtpSecure": True,
        "smtpRequireTls": False,
        "unreadCount": 0,
    }


def _portable_account_from_external(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    email = _normalize_mailbox_address(item.get("email", ""))
    if not email:
        return None
    smtp = item.get("smtp") if isinstance(item.get("smtp"), dict) else {}
    return {
        "id": str(item.get("id") or email),
        "type": "external",
        "address": email,
        "displayName": _normalize_display_name(item.get("displayName", ""), email),
        "sendName": _normalize_display_name(item.get("sendName", ""), ""),
        "group": _normalize_account_group(item.get("group", "")),
        "provider": str(item.get("name") or item.get("provider") or "custom"),
        "unreadCount": _safe_int((item.get("syncStatus") or {}).get("unseen", item.get("unreadCount", 0))),
        "host": str(item.get("host") or ""),
        "port": _safe_int(item.get("port"), 993),
        "secure": bool(item.get("secure", True)),
        "smtpHost": str(smtp.get("host") or ""),
        "smtpPort": _safe_int(smtp.get("port"), 465),
        "smtpSecure": bool(smtp.get("secure", True)),
        "smtpRequireTls": bool(smtp.get("requireTLS", False)),
    }


def _portable_sanitize_account_profile(item: dict) -> dict:
    safe = dict(item or {})
    blocked = {
        "password",
        "protectedpassword",
        "pass",
        "token",
        "accesstoken",
        "refreshtoken",
        "oauth",
        "oauthtokens",
        "apikey",
        "api_key",
        "secret",
    }
    for key in list(safe.keys()):
        normalized_key = str(key).replace("_", "").lower()
        if (
            normalized_key in blocked
            or "password" in normalized_key
            or "token" in normalized_key
            or "apikey" in normalized_key
            or "secret" in normalized_key
        ):
            safe.pop(key, None)
    if str(safe.get("type") or "").strip().lower() == "external":
        safe["protectedPassword"] = ""
    return safe


def _portable_account_ref(account: dict) -> str:
    return f"{account.get('type')}:{account.get('id')}".strip().lower()


def _portable_item_account_ref(item: dict) -> str:
    return f"{item.get('accountType') or item.get('account_type')}:{item.get('accountId') or item.get('account_id')}".strip().lower()


def _portable_query_account_refs() -> set[str]:
    raw = request.args.get("account_refs") or request.args.get("accounts") or ""
    refs = re.split(r"[\s,，]+", str(raw))
    return {ref.strip().lower() for ref in refs if ref.strip()}


def _portable_account_selected(account: dict, selected_refs: set[str], has_group_filter: bool, group_filter: str) -> bool:
    if selected_refs and _portable_account_ref(account) not in selected_refs:
        return False
    if has_group_filter and _normalize_account_group(account.get("group", "")) != group_filter:
        return False
    return True


def _portable_export_has_filter(selected_refs: set[str], has_group_filter: bool) -> bool:
    return bool(selected_refs) or has_group_filter


def _portable_keyword_rule_matches_export(
    item: dict,
    has_account_filter: bool,
    exported_account_refs: set[str],
    exported_account_groups: set[str],
) -> bool:
    normalized = _normalize_keyword_rule(item)
    if not normalized:
        return False
    if not has_account_filter:
        return True
    scope_type = normalized.get("scope_type", "all")
    if scope_type == "all":
        return True
    if scope_type == "group":
        scope_group = _normalize_account_group(normalized.get("scope_group", ""))
        return scope_group in exported_account_groups
    if scope_type == "accounts":
        scope_refs = {str(ref).strip().lower() for ref in normalized.get("scope_accounts", []) if str(ref).strip()}
        return bool(scope_refs & exported_account_refs)
    return False


def _portable_folder(account: dict, key: str, title: str, count: int = 0) -> dict:
    return {
        "accountType": account.get("type", ""),
        "accountId": account.get("id", ""),
        "accountAddress": account.get("address", ""),
        "key": key,
        "title": title or key,
        "count": _safe_int(count),
    }


def _portable_address_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(
            text for text in (_portable_address_text(item) for item in value)
            if text
        )
    if isinstance(value, dict):
        address = str(value.get("address") or value.get("email") or "").strip()
        name = str(value.get("name") or value.get("displayName") or "").strip()
        if name and address:
            return f"{name} <{address}>"
        return address or name
    return str(value or "").strip()


def _portable_intro(text: str = "", html: str = "", fallback: str = "") -> str:
    source = text or _extract_plain_text(html or "") or fallback
    return re.sub(r"\s+", " ", str(source or "")).strip()[:300]


def _portable_mail_date(item: dict, *names: str) -> str:
    for name in names:
        value = item.get(name)
        if value:
            return str(value)
    return ""


def _portable_apply_message_meta(settings: dict, message: dict) -> dict:
    meta = _public_message_meta(_get_message_meta(
        settings,
        message.get("accountType", ""),
        message.get("accountId", ""),
        message.get("folder", ""),
        message.get("id", ""),
    ))
    if any([meta.get("favorite"), meta.get("pinned"), meta.get("color")]):
        message["favorite"] = bool(meta.get("favorite"))
        message["pinned"] = bool(meta.get("pinned"))
        message["meta"] = meta
    return message


def _portable_message_from_local(mail: dict, email: str, folder: str, label: str = "") -> dict:
    text = str(mail.get("text") or "")
    html = str(mail.get("html") or "")
    return {
        "id": str(mail.get("id") or mail.get("msgid") or ""),
        "accountType": "local",
        "accountId": email,
        "accountLabel": label or email,
        "folder": folder,
        "from": _portable_address_text(mail.get("from")),
        "to": _portable_address_text(mail.get("to")),
        "cc": _portable_address_text(mail.get("cc")),
        "bcc": _portable_address_text(mail.get("bcc")),
        "subject": mail.get("subject", ""),
        "intro": mail.get("intro") or _portable_intro(text, html),
        "date": _portable_mail_date(mail, "createdAt", "updatedAt", "date"),
        "seen": bool(mail.get("seen")),
        "favorite": bool((mail.get("meta") or {}).get("favorite")),
        "pinned": bool((mail.get("meta") or {}).get("pinned")),
        "html": html,
        "text": text,
        "error": "",
        "attachments": _format_attachments(mail),
    }


def _portable_message_from_local_sent(mail: dict, email: str, label: str = "") -> dict:
    text = str(mail.get("text") or "")
    html = str(mail.get("html") or "")
    return {
        "id": str(mail.get("id") or ""),
        "accountType": "local",
        "accountId": email,
        "accountLabel": label or email,
        "folder": "sent",
        "from": _portable_address_text(mail.get("from") or mail.get("from_address") or email),
        "to": _portable_address_text(mail.get("to")),
        "cc": _portable_address_text(mail.get("cc")),
        "bcc": _portable_address_text(mail.get("bcc")),
        "subject": mail.get("subject", ""),
        "intro": mail.get("intro") or _portable_intro(text, html),
        "date": _portable_mail_date(mail, "createdAt", "updatedAt", "date"),
        "seen": True,
        "favorite": bool((mail.get("meta") or {}).get("favorite")),
        "pinned": bool((mail.get("meta") or {}).get("pinned")),
        "html": html,
        "text": text,
        "error": "",
        "attachments": mail.get("attachments", []) if isinstance(mail.get("attachments"), list) else [],
    }


def _portable_message_from_draft(item: dict) -> dict:
    text = str(item.get("text") or "")
    html = str(item.get("html") or "")
    account_id = item.get("account_id") or item.get("from_email") or ""
    return {
        "id": str(item.get("id") or ""),
        "accountType": item.get("account_type", "local"),
        "accountId": account_id,
        "accountLabel": item.get("from_email") or account_id,
        "folder": "drafts",
        "from": _portable_address_text({"name": item.get("from_name", ""), "address": item.get("from_email", "")}),
        "to": _portable_address_text(item.get("to")),
        "cc": _portable_address_text(item.get("cc")),
        "bcc": _portable_address_text(item.get("bcc")),
        "subject": item.get("subject", ""),
        "intro": _portable_intro(text, html),
        "date": _portable_mail_date(item, "updated_at", "created_at"),
        "seen": True,
        "favorite": False,
        "pinned": False,
        "html": html,
        "text": text,
        "error": "",
        "attachments": item.get("attachments", []) if isinstance(item.get("attachments"), list) else [],
        "draftVersion": item.get("version", 1),
    }


def _portable_message_from_outbox(item: dict) -> dict:
    message = _portable_message_from_draft(item)
    message["folder"] = "outbox"
    message["error"] = item.get("error", "")
    message["status"] = item.get("status", "failed")
    message["attempts"] = item.get("attempts", 0)
    return message


def _portable_message_from_external(mail: dict, account: dict) -> dict:
    text = str(mail.get("text") or "")
    html = str(mail.get("html") or "")
    return {
        "id": str(mail.get("uid") or mail.get("id") or ""),
        "accountType": "external",
        "accountId": str(account.get("id") or ""),
        "accountLabel": account.get("displayName") or account.get("email") or "",
        "folder": str(mail.get("folder") or "INBOX"),
        "from": _portable_address_text(mail.get("from")),
        "to": _portable_address_text(mail.get("to")),
        "cc": _portable_address_text(mail.get("cc")),
        "bcc": _portable_address_text(mail.get("bcc")),
        "subject": mail.get("subject", ""),
        "intro": mail.get("intro") or _portable_intro(text, html),
        "date": _portable_mail_date(mail, "date", "createdAt", "updatedAt"),
        "seen": bool(mail.get("seen")),
        "favorite": bool((mail.get("meta") or {}).get("favorite") or mail.get("flagged")),
        "pinned": bool((mail.get("meta") or {}).get("pinned")),
        "html": html,
        "text": text,
        "error": "",
        "attachments": mail.get("attachments", []) if isinstance(mail.get("attachments"), list) else [],
    }


def _portable_parse_message_limit() -> int | None:
    raw = str(request.args.get("limit", "200")).strip().lower()
    if raw in {"all", "*", "-1", "full"}:
        return None
    return min(max(_safe_int(raw, 200), 0), 2000)


def _portable_limit_reached(messages: list, limit: int | None) -> bool:
    return limit is not None and len(messages) >= limit


def _portable_remaining(limit: int | None, messages: list, page_size: int = 100) -> int:
    if limit is None:
        return page_size
    return max(0, min(page_size, limit - len(messages)))


def _portable_json_get(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: int = 30) -> dict | list | None:
    try:
        resp = http_session.get(url, params=params or {}, headers=headers or {}, timeout=timeout)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def _portable_fetch_duckmail_page(base_url: str, path: str, headers: dict, offset: int, limit: int, params: dict | None = None) -> tuple[list, int | None]:
    data = _portable_json_get(
        f"{base_url}{path}",
        params={**(params or {}), "offset": offset, "limit": limit},
        headers=headers,
        timeout=30,
    )
    if isinstance(data, dict):
        items = data.get("hydra:member", [])
        return (items if isinstance(items, list) else []), _safe_int(data.get("hydra:totalItems"), -1)
    return (data if isinstance(data, list) else []), None


def _portable_fetch_duckmail_total(base_url: str, path: str, headers: dict, params: dict | None = None) -> int:
    _, total = _portable_fetch_duckmail_page(base_url, path, headers, 0, 1, params=params)
    return max(0, total or 0)


def _portable_enrich_local_message(base_url: str, headers: dict, mail: dict) -> dict:
    message_id = str(mail.get("id") or mail.get("msgid") or "").strip()
    if not message_id:
        return mail
    detail = _portable_json_get(
        f"{base_url}/messages/{quote(message_id, safe='')}",
        params={"markSeen": "0"},
        headers=headers,
        timeout=30,
    )
    if isinstance(detail, dict):
        merged = {**mail, **detail}
        merged["attachments"] = _format_attachments(merged)
        return merged
    return mail


def _portable_collect_local_folder(
    settings: dict,
    account: dict,
    headers: dict,
    path: str,
    folder: str,
    limit: int | None,
    messages: list,
    counts: dict,
    *,
    base_url: str,
    sent: bool = False,
):
    email = account["address"]
    offset = 0
    total = _portable_fetch_duckmail_total(base_url, path, headers)
    counts[folder] = total
    while not _portable_limit_reached(messages, limit):
        page_limit = _portable_remaining(limit, messages)
        if page_limit <= 0:
            break
        page, total_from_page = _portable_fetch_duckmail_page(base_url, path, headers, offset, page_limit)
        if total_from_page is not None and total_from_page >= 0:
            counts[folder] = total_from_page
        if not page:
            break
        _attach_message_meta(settings, page, "local", email, folder)
        for item in page:
            if not isinstance(item, dict) or _portable_limit_reached(messages, limit):
                continue
            mail = item if sent else _portable_enrich_local_message(base_url, headers, item)
            portable = _portable_message_from_local_sent(mail, email, account.get("displayName", "")) if sent else _portable_message_from_local(mail, email, folder, account.get("displayName", ""))
            messages.append(_portable_apply_message_meta(settings, portable))
        offset += len(page)
        if len(page) < page_limit or (counts.get(folder, 0) and offset >= counts[folder]):
            break


def _portable_collect_local_messages(settings: dict, account: dict, limit: int | None) -> tuple[list, dict]:
    messages = []
    counts = {"inbox": 0, "unread": 0, "sent": 0, "drafts": 0, "outbox": 0, "trash": 0}
    email = account["address"]
    token, err = _get_mail_token(email, "")
    if not err:
        base_url = DUCKMAIL_BASE_URL.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        try:
            counts["unread"] = _portable_fetch_duckmail_total(base_url, "/messages", headers, {"unread": "true"})
            _portable_collect_local_folder(settings, account, headers, "/messages", "inbox", limit, messages, counts, base_url=base_url)
            _portable_collect_local_folder(settings, account, headers, "/sent", "sent", limit, messages, counts, base_url=base_url, sent=True)
            _portable_collect_local_folder(settings, account, headers, "/messages/trash", "trash", limit, messages, counts, base_url=base_url)
        except Exception as exc:
            app.logger.warning("便携导出读取本地邮件失败: %s %s", email, exc)

    drafts = [
        _public_draft(item)
        for item in _settings_list(settings, "drafts")
        if item.get("account_id") == email or item.get("from_email") == email
    ]
    outbox = [
        _public_outbox(item)
        for item in _settings_list(settings, "outbox")
        if item.get("account_id") == email or item.get("from_email") == email
    ]
    counts["drafts"] = len(drafts)
    counts["outbox"] = len(outbox)
    for item in drafts:
        if _portable_limit_reached(messages, limit):
            break
        messages.append(_portable_message_from_draft(item))
    for item in outbox:
        if _portable_limit_reached(messages, limit):
            break
        messages.append(_portable_message_from_outbox(item))
    counts["all"] = sum(counts.get(name, 0) for name in ("inbox", "sent", "drafts", "outbox", "trash"))
    counts["truncated"] = limit is not None and len(messages) < counts["all"]
    return messages, counts


def _portable_set_folder_count(folders: list, account: dict, key: str, count: int):
    for folder in folders:
        if (
            folder.get("accountType") == account.get("type")
            and folder.get("accountId") == account.get("id")
            and folder.get("key") == key
        ):
            folder["count"] = _safe_int(count)
            return


def _portable_get_folder_count(folders: list, account: dict, key: str) -> int:
    for folder in folders:
        if (
            folder.get("accountType") == account.get("type")
            and folder.get("accountId") == account.get("id")
            and folder.get("key") == key
        ):
            return _safe_int(folder.get("count"), 0)
    return 0


def _portable_append_folder(folders: list, folder_keys: set, folder: dict):
    key = _portable_folder_key(folder)
    if key and key not in folder_keys:
        folders.append(folder)
        folder_keys.add(key)


def _portable_external_account_url(account: dict, suffix: str = "") -> str:
    account_id = quote(str(account.get("id") or ""), safe="")
    return urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", f"api/accounts/{account_id}{suffix}")


def _portable_external_folders(account: dict) -> list:
    folders = [
        _portable_folder(account, "__memail_all__", "所有邮件"),
        _portable_folder(account, "__memail_unread__", "未读邮件"),
    ]
    status_by_path = {}
    status_data = _portable_json_get(_portable_external_account_url(account, "/folders/status"), timeout=20)
    if isinstance(status_data, list):
        status_by_path = {
            str(item.get("path") or ""): item
            for item in status_data
            if isinstance(item, dict) and item.get("path")
        }
    folder_data = _portable_json_get(_portable_external_account_url(account, "/folders"), timeout=20)
    if isinstance(folder_data, list) and folder_data:
        for item in folder_data:
            if not isinstance(item, dict) or item.get("noselect"):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            status = status_by_path.get(path, {})
            folders.append(_portable_folder(
                account,
                path,
                item.get("name") or path,
                _safe_int(status.get("messages"), 0),
            ))
        return folders
    folders.extend([
        _portable_folder(account, "INBOX", "收件箱"),
        _portable_folder(account, "Sent", "已发送"),
        _portable_folder(account, "Drafts", "草稿箱"),
        _portable_folder(account, "Trash", "已删除"),
    ])
    return folders


def _portable_enrich_external_message(account: dict, mail: dict) -> dict:
    uid = str(mail.get("uid") or mail.get("id") or "").strip()
    folder = str(mail.get("folder") or "INBOX")
    if not uid:
        return mail
    detail = _portable_json_get(
        _portable_external_account_url(account, f"/mails/{quote(uid, safe='')}"),
        params={"folder": folder, "markSeen": "0"},
        timeout=30,
    )
    if isinstance(detail, dict):
        return {**mail, **detail}
    return mail


def _portable_collect_external_messages(settings: dict, account: dict, limit: int | None) -> tuple[list, dict]:
    messages = []
    counts = {"__memail_all__": 0, "__memail_unread__": 0, "truncated": False}
    offset = 0
    while not _portable_limit_reached(messages, limit):
        page_limit = _portable_remaining(limit, messages)
        if page_limit <= 0:
            break
        data = _portable_json_get(
            _portable_external_account_url(account, "/mails"),
            params={
                "folder": "__memail_all__",
                "count": page_limit,
                "offset": offset,
                "cacheOnly": "1",
            },
            timeout=30,
        )
        if not isinstance(data, dict):
            break
        page = data.get("mails", [])
        if not isinstance(page, list) or not page:
            counts["__memail_all__"] = _safe_int(data.get("total"), len(messages))
            counts["__memail_unread__"] = _safe_int(data.get("unseen"), counts["__memail_unread__"])
            break
        counts["__memail_all__"] = _safe_int(data.get("total"), counts["__memail_all__"])
        counts["__memail_unread__"] = _safe_int(data.get("unseen"), counts["__memail_unread__"])
        for item in page:
            if not isinstance(item, dict) or _portable_limit_reached(messages, limit):
                continue
            mail = _portable_enrich_external_message(account, item)
            portable = _portable_message_from_external(mail, account)
            messages.append(_portable_apply_message_meta(settings, portable))
        offset += len(page)
        if not data.get("hasMore") or len(page) < page_limit:
            break
    if counts["__memail_all__"] <= 0:
        counts["__memail_all__"] = len(messages)
    counts["truncated"] = limit is not None and len(messages) < counts["__memail_all__"]
    return messages, counts


def _public_ai_settings_for_portable() -> dict:
    ai = _get_ai_settings()
    channels = []
    for item in ai.get("channels", []):
        safe = _safe_ai_channel(item)
        channels.append({
            "id": safe.get("id", ""),
            "name": safe.get("name", ""),
            "provider": safe.get("provider", ""),
            "baseUrl": safe.get("base_url", ""),
            "models": safe.get("models", []),
            "updatedAt": safe.get("updated_at", ""),
        })
    default_model = ai.get("default_model", {}) if isinstance(ai.get("default_model"), dict) else {}
    return {
        "channels": channels,
        "defaultModel": {
            "channelId": default_model.get("channel_id", ""),
            "model": default_model.get("model", ""),
        },
    }


def _public_translation_settings_for_portable() -> dict:
    settings = _safe_translation_settings(_get_translation_settings())
    return {
        "defaultProvider": settings.get("default_provider", "ai"),
        "fallbackToAi": bool(settings.get("fallback_to_ai", True)),
        "baidu": {
            "appid": (settings.get("baidu") or {}).get("appid", ""),
            "secretConfigured": bool((settings.get("baidu") or {}).get("secret_configured")),
        },
        "tencent": {
            "secretId": (settings.get("tencent") or {}).get("secret_id", ""),
            "secretKeyConfigured": bool((settings.get("tencent") or {}).get("secret_key_configured")),
            "region": (settings.get("tencent") or {}).get("region", "ap-guangzhou"),
        },
        "googleCloud": {
            "apiKeyConfigured": bool((settings.get("google_cloud") or {}).get("api_key_configured")),
        },
    }


def _normalize_portable_ai_settings(value: dict) -> dict:
    if not isinstance(value, dict):
        return {"channels": [], "default_model": {}}
    channels = []
    for item in value.get("channels", []):
        if not isinstance(item, dict):
            continue
        channel_id = str(item.get("id") or _new_id("ai_"))
        provider = _normalize_ai_provider(item.get("provider", ""))
        if not provider:
            continue
        channel = {
            "id": channel_id,
            "name": str(item.get("name") or item.get("displayName") or "AI Channel")[:80],
            "provider": provider,
            "base_url": _normalize_ai_base_url(provider, item.get("base_url") or item.get("baseUrl") or ""),
            "models": [str(model) for model in item.get("models", []) if str(model).strip()][:500],
            "created_at": item.get("created_at") or item.get("createdAt") or _iso_now(),
            "updated_at": item.get("updated_at") or item.get("updatedAt") or _iso_now(),
        }
        channels.append(channel)
    default_model = value.get("default_model") or value.get("defaultModel") or {}
    if isinstance(default_model, dict):
        default_model = {
            "channel_id": default_model.get("channel_id") or default_model.get("channelId") or "",
            "model": default_model.get("model") or "",
        }
    else:
        default_model = {}
    return {"channels": channels, "default_model": default_model}


def _normalize_portable_translation_settings(value: dict) -> dict:
    if not isinstance(value, dict):
        return {}
    default_provider = (value.get("default_provider") or value.get("defaultProvider") or "").strip().lower()
    result = {}
    if default_provider in TRANSLATION_PROVIDERS:
        result["default_provider"] = default_provider
    if "fallback_to_ai" in value or "fallbackToAi" in value:
        result["fallback_to_ai"] = bool(value.get("fallback_to_ai", value.get("fallbackToAi")))
    baidu = value.get("baidu") if isinstance(value.get("baidu"), dict) else {}
    if baidu.get("appid"):
        result["baidu_appid"] = str(baidu.get("appid")).strip()
    tencent = value.get("tencent") if isinstance(value.get("tencent"), dict) else {}
    if tencent.get("secretId") or tencent.get("secret_id"):
        result["tencent_secret_id"] = str(tencent.get("secretId") or tencent.get("secret_id")).strip()
    if tencent.get("region"):
        result["tencent_region"] = str(tencent.get("region")).strip()
    google_cloud = value.get("googleCloud") or value.get("google_cloud")
    if isinstance(google_cloud, dict) and google_cloud.get("apiKey"):
        result["google_cloud_api_key"] = str(google_cloud.get("apiKey")).strip()
    return result


def _portable_message_key(item: dict) -> str:
    return "|".join([
        str(item.get("accountType") or item.get("account_type") or "").strip().lower(),
        str(item.get("accountId") or item.get("account_id") or "").strip().lower(),
        str(item.get("folder") or "").strip(),
        str(item.get("id") or item.get("message_id") or "").strip(),
    ])


def _portable_folder_key(item: dict) -> str:
    return "|".join([
        str(item.get("accountType") or item.get("account_type") or "").strip().lower(),
        str(item.get("accountId") or item.get("account_id") or "").strip().lower(),
        str(item.get("accountAddress") or item.get("account_address") or "").strip().lower(),
        str(item.get("key") or "").strip(),
    ])


def _merge_portable_ai_settings(settings: dict, incoming: dict) -> int:
    if not incoming.get("channels"):
        return 0
    existing_ai = settings.get("ai", {}) if isinstance(settings.get("ai"), dict) else {}
    existing_channels = existing_ai.get("channels", []) if isinstance(existing_ai.get("channels"), list) else []
    existing_by_id = {
        str(item.get("id")): item
        for item in existing_channels
        if isinstance(item, dict) and item.get("id")
    }
    merged = []
    for channel in incoming.get("channels", []):
        if not isinstance(channel, dict):
            continue
        old = existing_by_id.get(str(channel.get("id")))
        if old and old.get("api_key"):
            channel["api_key"] = old["api_key"]
        merged.append(channel)
    settings["ai"] = {
        "channels": merged,
        "default_model": incoming.get("default_model", {}),
    }
    return len(merged)


def _device_auth(required_scope: str | None = None) -> dict | None:
    token = _extract_device_token()
    if not token:
        return None
    settings = _read_viewer_settings()
    token_hash = _hash_token(token)
    now_dt = _utc_now()
    now = now_dt.isoformat()
    for item in _settings_list(settings, "device_tokens"):
        if item.get("token_hash") == token_hash and not item.get("revoked"):
            scopes = item.get("scopes") if isinstance(item.get("scopes"), list) else []
            normalized_scopes = {str(scope).strip() for scope in scopes if str(scope).strip()}
            if required_scope and "*" not in normalized_scopes and "client:full" not in normalized_scopes and required_scope not in normalized_scopes:
                return None
            last_seen = _safe_iso_datetime(item.get("last_seen", ""))
            if not last_seen or (now_dt - last_seen).total_seconds() >= 60:
                item["last_seen"] = now
                item["last_ip"] = _client_ip()
                _write_viewer_settings(settings)
            return {"id": item.get("id"), "name": item.get("name", ""), "scopes": scopes}
    return None


def device_or_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = _device_auth("sync:read")
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


@app.before_request
def ensure_background_workers():
    _ensure_extraction_scanner()
    return None


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if _admin_auth_enabled() and not session.get("authenticated"):
            auth = _device_auth("client:full")
            if auth:
                request.device_auth = auth
                return f(*args, **kwargs)
            if _wants_json_response():
                return jsonify({"success": False, "message": "未授权访问"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function


def extraction_api_required(f):
    """允许后台登录或具备 extraction:read 权限的设备 Token 读取抽取结果。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = _device_auth("extraction:read")
        if auth:
            request.device_auth = auth
            return f(*args, **kwargs)
        return login_required(f)(*args, **kwargs)
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
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)</?(html|head|body|meta|title)\b[^>]*>", " ", html)
    return html.strip()


def _prepare_html_for_render(html: str) -> str:
    return _sanitize_email_html(html)


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


def _normalize_send_attachments(attachments, keep_content: bool = False) -> list:
    if not isinstance(attachments, list):
        return []
    normalized = []
    total_size = 0
    for index, item in enumerate(attachments[:20]):
        if not isinstance(item, dict):
            continue
        filename = (item.get("filename") or item.get("name") or f"attachment_{index}").strip()
        content = (item.get("content") or "").strip()
        if not filename or not content:
            continue
        content_type = (
            item.get("content_type")
            or item.get("contentType")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        try:
            raw = base64.b64decode(content, validate=True)
        except Exception:
            continue
        if len(raw) > MAX_SEND_ATTACHMENT_BYTES:
            continue
        total_size += len(raw)
        if total_size > MAX_SEND_ATTACHMENTS_BYTES:
            break
        payload = {
            "filename": filename,
            "content_type": content_type,
            "size": len(raw),
        }
        if keep_content:
            payload["content"] = content
        normalized.append(payload)
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


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")


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

    started_at = time.time()
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
            if IMAGE_PROXY_TOTAL_TIMEOUT_SECONDS > 0 and time.time() - started_at > IMAGE_PROXY_TOTAL_TIMEOUT_SECONDS:
                raise requests.Timeout(f"image proxy exceeded {IMAGE_PROXY_TOTAL_TIMEOUT_SECONDS}s")
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_PROXY_BYTES:
                return jsonify({"success": False, "message": "图片过大"}), 413
            chunks.append(chunk)
    except requests.RequestException as e:
        app.logger.warning("图片代理读取失败: url=%s error=%s", source_url, e)
        return jsonify({"success": False, "message": "图片加载超时或远程不可用"}), 504
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


@app.route("/api/translation/settings", methods=["GET"])
@login_required
def get_translation_settings():
    return jsonify({"success": True, "settings": _safe_translation_settings(_get_translation_settings())})


@app.route("/api/translation/settings", methods=["POST"])
@login_required
def save_translation_settings():
    data = request.get_json(silent=True) or {}
    try:
        translation = _write_translation_settings(data)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "settings": _safe_translation_settings(translation)})


@app.route("/api/ai/translate", methods=["POST"])
@login_required
def translate_mail_to_chinese():
    started_at = time.time()
    provider = ""
    meta = {}
    try:
        data = request.get_json(silent=True) or {}
        html_content = (data.get("html", "") or "").strip()
        text_content = (data.get("text", "") or "").strip()
        subject_raw = (data.get("subject", "") or "").strip()
        force_refresh = bool(data.get("force_refresh") or data.get("forceRefresh"))
        html_detected = bool(html_content)
        subject = _extract_plain_text(subject_raw)
        app.logger.info(
            "邮件翻译开始: subject=%s html_detected=%s html_chars=%s text_chars=%s force_refresh=%s",
            subject[:120],
            html_detected,
            len(html_content),
            len(text_content),
            force_refresh,
        )
        if not html_content and not text_content and not subject:
            return jsonify({"success": False, "message": "没有可翻译的邮件内容"}), 400

        translation_settings = _get_translation_settings()
        provider = translation_settings.get("default_provider", "ai")
        fallback_to_ai = bool(translation_settings.get("fallback_to_ai", True))
        identity = _translation_request_identity(data)
        source_hash = _translation_source_hash(subject_raw, html_content, text_content)
        cache_key = _translation_cache_key(identity, provider, source_hash)
        if not force_refresh:
            settings_for_cache = _read_viewer_settings()
            cached = _read_translation_cache_item(settings_for_cache, cache_key)
            if cached:
                try:
                    cached["last_used_at"] = _iso_now()
                    cached["hits"] = int(cached.get("hits", 0)) + 1
                    _write_translation_cache_item(settings_for_cache, cache_key, cached)
                except Exception as cache_error:
                    app.logger.warning("邮件翻译缓存命中后更新失败: %s", cache_error, exc_info=True)
                elapsed_ms = int((time.time() - started_at) * 1000)
                app.logger.info(
                    "邮件翻译缓存命中: key=%s provider=%s identity=%s elapsed_ms=%s",
                    cache_key[:12],
                    provider,
                    identity,
                    elapsed_ms,
                )
                return jsonify({
                    "success": True,
                    "translation": cached.get("translation", ""),
                    "format": cached.get("format", "text"),
                    "engine": cached.get("engine", ""),
                    "provider": cached.get("provider", ""),
                    "model": cached.get("model", ""),
                    "fallback_from": cached.get("fallback_from", ""),
                    "elapsed_ms": elapsed_ms,
                    "input_chars": cached.get("input_chars", 0),
                    "original_chars": cached.get("input_chars", 0),
                    "truncated": False,
                    "cached": True,
                    "cache_saved": True,
                    "cache_created_at": cached.get("created_at", ""),
                })
        try:
            translated, meta = _call_configured_translation_engine(provider, html_content, text_content, subject, translation_settings)
        except Exception as primary_error:
            if provider != "ai" and fallback_to_ai:
                app.logger.warning(
                    "专用翻译失败，切换 AI 兜底: provider=%s error=%s",
                    provider,
                    primary_error,
                    exc_info=True,
                )
                ai_content = _strip_layout_html_for_translation(html_content) if html_content else _normalize_translation_source_text(text_content)
                if subject:
                    ai_content = f"邮件主题：{subject}\n\n{ai_content}"
                translated, meta = _call_ai_translation_engine(ai_content, bool(html_content))
                translated = _normalize_ai_translation(translated, bool(html_content))
                meta["fallback_from"] = provider
                meta["input_chars"] = len(ai_content)
            else:
                raise
    except (requests.Timeout, requests.ConnectionError) as e:
        app.logger.warning(
            "邮件翻译网络超时/连接失败: provider=%s chars=%s ai_timeout=%s translation_timeout=%s error=%s",
            provider,
            meta.get("input_chars", 0) if isinstance(meta, dict) else 0,
            AI_TRANSLATION_TIMEOUT_SECONDS,
            TRANSLATION_SERVICE_TIMEOUT_SECONDS,
            e,
        )
        return jsonify({
            "success": False,
            "message": "翻译请求超时，请稍后重试或切换翻译渠道",
        }), 504
    except Exception as e:
        elapsed_ms = int((time.time() - started_at) * 1000)
        app.logger.warning("邮件翻译失败: %s elapsed_ms=%s", e, elapsed_ms, exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e) or "翻译服务异常，请查看 mail-viewer 日志",
            "elapsed_ms": elapsed_ms,
        }), 502
    if not (translated or "").strip():
        app.logger.warning("邮件翻译失败: 返回空内容 provider=%s input_chars=%s", meta.get("provider", ""), meta.get("input_chars", 0))
        return jsonify({"success": False, "message": "翻译返回空内容，请更换翻译渠道后重试"}), 502
    response_format = meta.get("format", "text") if isinstance(meta, dict) else "text"
    elapsed_ms = int((time.time() - started_at) * 1000)
    cache_saved = False
    if isinstance(meta, dict) and not meta.get("fallback_from"):
        try:
            now = _iso_now()
            cache_item = {
                "translation": translated,
                "format": response_format,
                "engine": meta.get("engine", ""),
                "provider": meta.get("provider", ""),
                "model": meta.get("model", ""),
                "fallback_from": meta.get("fallback_from", ""),
                "input_chars": meta.get("input_chars", 0),
                "output_chars": len(translated),
                "requested_provider": provider,
                "identity": identity,
                "source_hash": source_hash,
                "created_at": now,
                "last_used_at": now,
                "hits": 0,
            }
            cache_saved = _write_translation_cache_item(_read_viewer_settings(), cache_key, cache_item)
        except Exception as cache_error:
            app.logger.warning("邮件翻译缓存保存失败: %s", cache_error, exc_info=True)
    elif isinstance(meta, dict):
        app.logger.info("邮件翻译缓存跳过: fallback_from=%s", meta.get("fallback_from", ""))
    app.logger.info(
        "邮件翻译完成: engine=%s provider=%s model=%s format=%s input_chars=%s output_chars=%s elapsed_ms=%s cache_saved=%s preview=%s",
        meta.get("engine", ""),
        meta.get("provider", ""),
        meta.get("model", ""),
        response_format,
        meta.get("input_chars", 0),
        len(translated),
        elapsed_ms,
        cache_saved,
        _translation_preview(translated),
    )
    return jsonify({
        "success": True,
        "translation": translated,
        "format": response_format,
        "engine": meta.get("engine", ""),
        "provider": meta.get("provider", ""),
        "model": meta.get("model", ""),
        "fallback_from": meta.get("fallback_from", ""),
        "elapsed_ms": elapsed_ms,
        "input_chars": meta.get("input_chars", 0),
        "original_chars": meta.get("input_chars", 0),
        "truncated": False,
        "cached": False,
        "cache_saved": cache_saved,
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
@app.route("/api/inbox/detail/cache", methods=["POST"])
@login_required
def inbox_detail():
    """通用收件箱邮件详情"""
    data = request.json or {}
    email = data.get("email", "").strip()
    password = data.get("password", "").strip() or _get_unified_password()
    message_id = data.get("message_id", "").strip()
    mark_seen = False if request.path.endswith("/cache") else bool(data.get("mark_seen", data.get("markSeen", True)))

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
            params={"markSeen": "1" if mark_seen else "0"},
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


@app.route("/api/extraction-rules", methods=["GET"])
@login_required
def list_extraction_rules():
    _ensure_extraction_scanner()
    settings = _read_viewer_settings()
    rules = [
        _public_extraction_rule(item)
        for item in _settings_list(settings, "extraction_rules")
        if _normalize_extraction_rule(item)
    ]
    return jsonify({"success": True, "rules": rules})


@app.route("/api/extraction-rules", methods=["POST"])
@login_required
def save_extraction_rule():
    payload, status = _save_extraction_rule_payload(request.json or {})
    return jsonify(payload), status


def _save_extraction_rule_payload(data: dict) -> tuple[dict, int]:
    _ensure_extraction_scanner()
    settings = _read_viewer_settings()
    rules = [
        _normalize_extraction_rule(item)
        for item in _settings_list(settings, "extraction_rules")
    ]
    rules = [item for item in rules if item]
    now = _iso_now()
    incoming = _normalize_extraction_rule({
        **data,
        "updated_at": now,
        "created_at": data.get("created_at") or now,
    })
    if not incoming:
        return {"success": False, "message": "抽取规则缺少名称、匹配条件或物流单号正则"}, 400
    existing = next((item for item in rules if item.get("id") == incoming["id"]), None)
    if existing:
        incoming["created_at"] = existing.get("created_at", incoming["created_at"])
        incoming["last_scan_at"] = existing.get("last_scan_at", "")
        incoming["last_scan_count"] = existing.get("last_scan_count", 0)
        incoming["last_scan_error"] = existing.get("last_scan_error", "")
        incoming["scan_state"] = existing.get("scan_state", {})
        existing.update(incoming)
    else:
        rules.append(incoming)
    settings["extraction_rules"] = rules[-200:]
    _touch_sync_event(settings, "extraction_rule.upserted", {"id": incoming["id"]})
    _write_viewer_settings(settings)

    scan_result = None
    if _bool_from_setting(data.get("scan_now"), False):
        scan_result = _run_extraction_scan_once(incoming["id"])
        settings = _read_viewer_settings()
        rules = [
            _normalize_extraction_rule(item)
            for item in _settings_list(settings, "extraction_rules")
        ]
        rules = [item for item in rules if item]
        incoming = next((item for item in rules if item.get("id") == incoming["id"]), incoming)

    return {
        "success": True,
        "rule": _public_extraction_rule(incoming),
        "rules": [_public_extraction_rule(item) for item in rules],
        "scan": scan_result,
    }, 200


@app.route("/api/extraction-rules/defaults/aosom-shipped", methods=["POST"])
@login_required
def save_aosom_shipped_extraction_rule():
    data = request.json or {}
    account_emails = data.get("account_emails", data.get("accountEmails", []))
    scan_now = _bool_from_setting(data.get("scan_now"), False)
    settings = _read_viewer_settings()
    existing_rule = None
    for item in _settings_list(settings, "extraction_rules"):
        rule = _normalize_extraction_rule(item)
        if rule and rule.get("template") == "aosom_shipped":
            existing_rule = rule
            break
    payload_data = {
        **data,
        "name": "Aosom CA/US 发货物流提取",
        "sender_contains": AOSOM_SENDER_CONTAINS,
        "subject_contains": "",
        "body_keywords": AOSOM_BODY_KEYWORDS,
        "template": "aosom_shipped",
        "account_emails": account_emails,
        "scan_now": scan_now,
    }
    if existing_rule:
        payload_data["id"] = existing_rule["id"]
    payload, status = _save_extraction_rule_payload(payload_data)
    if status >= 400:
        return jsonify(payload), status
    settings = _read_viewer_settings()
    rules = [
        _public_extraction_rule(item)
        for item in _settings_list(settings, "extraction_rules")
        if _normalize_extraction_rule(item)
    ]
    return jsonify({
        "success": True,
        "rules": rules,
        "created_rules": [payload["rule"]] if payload.get("rule") else [],
        "scan": payload.get("scan"),
    })


@app.route("/api/extraction-rules/<rule_id>", methods=["DELETE"])
@login_required
def delete_extraction_rule(rule_id):
    settings = _read_viewer_settings()
    original_rules = [
        _normalize_extraction_rule(item)
        for item in _settings_list(settings, "extraction_rules")
    ]
    deleted_rule = next((item for item in original_rules if item and item.get("id") == rule_id), None)
    rules = [item for item in original_rules if item and item.get("id") != rule_id]
    deleted_results = _delete_extraction_results(rule_id, deleted_rule)
    legacy_results = [
        item for item in _settings_list(settings, "extraction_results")
        if isinstance(item, dict)
    ]
    settings["extraction_results"] = [
        item for item in legacy_results
        if not _extraction_result_matches_rule_scope(item, deleted_rule, rule_id)
    ]
    deleted_results += max(0, len(legacy_results) - len(settings["extraction_results"]))
    settings["extraction_rules"] = rules
    _touch_sync_event(settings, "extraction_rule.deleted", {"id": rule_id, "deleted_results": deleted_results})
    _write_viewer_settings(settings)
    return jsonify({"success": True, "deleted_results": deleted_results, "rules": [_public_extraction_rule(item) for item in rules]})


@app.route("/api/extraction-rules/<rule_id>/scan", methods=["POST"])
@login_required
def scan_extraction_rule(rule_id):
    _ensure_extraction_scanner()
    result = _run_extraction_scan_once(rule_id)
    return jsonify({"success": True, **result})


@app.route("/api/extraction/scan", methods=["POST"])
@login_required
def scan_extraction_rules():
    _ensure_extraction_scanner()
    result = _run_extraction_scan_once()
    return jsonify({"success": True, **result})


@app.route("/api/extraction/results", methods=["GET"])
@app.route("/api/extraction-results", methods=["GET"])
@extraction_api_required
def list_extraction_results():
    rule_id = str(request.args.get("rule_id") or "").strip()
    order_number = str(request.args.get("order_number") or "").strip().lower()
    tracking_number = str(request.args.get("tracking_number") or "").strip().lower()
    account_email = _normalize_mailbox_address(request.args.get("account_email") or "").lower()
    limit = min(1000, max(1, _safe_int(request.args.get("limit"), 200)))
    offset = max(0, _safe_int(request.args.get("offset"), 0))
    page, total, store = _query_extraction_results(
        rule_id=rule_id,
        order_number=order_number,
        tracking_number=tracking_number,
        account_email=account_email,
        limit=limit,
        offset=offset,
    )
    return jsonify({
        "success": True,
        "total": total,
        "offset": offset,
        "limit": limit,
        "store": store,
        "results": page,
        "generated_at": _iso_now(),
    })


@app.route("/api/extraction/storage", methods=["GET"])
@login_required
def get_extraction_storage_status():
    settings = _read_viewer_settings()
    collection = _extraction_results_collection()
    if collection is None:
        return jsonify({
            "success": True,
            "store": "settings",
            "mongo": False,
            "legacy_results": len(_settings_list(settings, "extraction_results")),
            "migrated_at": settings.get("extraction_results_migrated_at", ""),
        })
    _migrate_extraction_results_to_mongo(settings)
    return jsonify({
        "success": True,
        "store": "mongo",
        "mongo": True,
        "database": MONGO_DB_NAME,
        "collection": "extraction_results",
        "total": collection.count_documents({}),
        "legacy_results": len(_settings_list(_read_viewer_settings(), "extraction_results")),
        "migrated_at": _read_viewer_settings().get("extraction_results_migrated_at", ""),
    })


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


@app.route("/api/trash/detail", methods=["POST"])
@login_required
def trash_detail():
    """查询回收站邮件详情。"""
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

        headers = {"Authorization": f"Bearer {token}"}
        last_resp = None
        for endpoint in (
            f"{base_url}/messages/trash/{message_id}",
            f"{base_url}/trash/{message_id}",
            f"{base_url}/messages/{message_id}",
        ):
            detail_resp = http_session.get(endpoint, headers=headers, timeout=30)
            last_resp = detail_resp
            if detail_resp.status_code == 200:
                detail = detail_resp.json()
                if isinstance(detail, dict):
                    detail["html"] = _prepare_html_for_render(detail.get("html", ""))
                    detail["attachments"] = _format_attachments(detail)
                    settings = _read_viewer_settings()
                    detail["meta"] = _public_message_meta(_get_message_meta(settings, "local", email, "trash", message_id))
                return jsonify({"success": True, "detail": detail})
            if detail_resp.status_code not in (404, 405):
                break

        detail = _extract_api_error(last_resp, "获取回收站详情失败") if last_resp else "获取回收站详情失败"
        return jsonify({"success": False, "message": detail})

    except Exception as e:
        app.logger.error(f"获取回收站详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务内部错误，请稍后重试"})


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
        "cc": data.get("cc", ""),
        "bcc": data.get("bcc", ""),
        "subject": data.get("subject", ""),
        "text": data.get("text", ""),
        "html": data.get("html", ""),
        "attachments": _normalize_send_attachments(data.get("attachments", []), keep_content=True),
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
                    "cc": item.get("cc", ""),
                    "bcc": item.get("bcc", ""),
                    "subject": item.get("subject", ""),
                    "text": item.get("text", ""),
                    "html": item.get("html", ""),
                    "fromName": item.get("from_name", ""),
                    "attachments": item.get("attachments", []),
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
        "cc": item.get("cc", ""),
        "bcc": item.get("bcc", ""),
        "subject": item.get("subject", ""),
        "text": item.get("text", ""),
        "html": item.get("html", ""),
        "attachments": item.get("attachments", []),
        "reply_to": item.get("reply_to", ""),
        "outbox_id": message_id,
    }
    return _send_local_email(payload, retry_item=item)


@app.route("/api/outbox/<message_id>", methods=["DELETE"])
@login_required
def delete_outbox(message_id):
    settings = _read_viewer_settings()
    outbox = _settings_list(settings, "outbox")
    before = len(outbox)
    settings["outbox"] = [item for item in outbox if item.get("id") != message_id]
    if len(settings["outbox"]) == before:
        return jsonify({"success": False, "message": "发送失败记录不存在"}), 404
    _touch_sync_event(settings, "outbox.deleted", {"id": message_id})
    _write_viewer_settings(settings)
    return jsonify({"success": True})


# ---- 安全管理 / 多端同步 API ----

@app.route("/api/security/confirm", methods=["POST"])
@login_required
def confirm_sensitive_action():
    data = request.json or {}
    password = data.get("password", "")
    code = data.get("totp_code", "")
    action = data.get("action", "*") or "*"
    username = session.get("username", "") or _get_admin_username()
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
    return jsonify({"success": True, "totp_enabled": _totp_enabled(settings)})


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
        "scopes": data.get("scopes") if isinstance(data.get("scopes"), list) else ["client:full"],
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


@app.route("/api/mobile/login", methods=["POST"])
def mobile_login():
    data = request.json or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    totp_code = str(data.get("totp_code") or data.get("totpCode") or "").strip()
    device_name = str(data.get("device_name") or data.get("deviceName") or "Android").strip()[:80] or "Android"
    if not _admin_auth_enabled():
        return jsonify({"success": False, "message": "服务端未启用管理员登录，无法签发移动端 Token"}), 403
    if _is_login_locked(username):
        return jsonify({"success": False, "message": "登录失败次数过多，请稍后再试"}), 429
    if not _verify_admin_credentials(username, password):
        _register_login_failure(username)
        settings = _read_viewer_settings()
        _append_audit(settings, "mobile.login.failed", {"username": username}, success=False)
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": "用户名或密码错误"}), 401

    settings = _read_viewer_settings()
    if _totp_enabled(settings) and not _verify_totp_code(_decrypt_setting(settings.get("totp_secret")), totp_code):
        _register_login_failure(username)
        _append_audit(settings, "mobile.login.totp_failed", {"username": username}, success=False)
        _write_viewer_settings(settings)
        return jsonify({"success": False, "message": "二次验证码错误", "totp_required": True}), 401

    _clear_login_failures(username)
    token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _iso_now()
    item = {
        "id": _new_id("dev_"),
        "name": device_name,
        "token_hash": _hash_token(token),
        "scopes": ["client:full"],
        "created_at": now,
        "last_seen": now,
        "last_ip": _client_ip(),
        "revoked": False,
    }
    tokens = _settings_list(settings, "device_tokens")
    tokens.append(item)
    settings["device_tokens"] = tokens[-100:]
    _append_audit(settings, "mobile.login", {"id": item["id"], "name": device_name, "username": username})
    _touch_sync_event(settings, "device.token.created", {"id": item["id"]})
    _write_viewer_settings(settings)
    return jsonify({
        "success": True,
        "token": token,
        "device": {k: v for k, v in item.items() if k != "token_hash"},
        "server_time": now,
    })


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
        "keywordRules": [
            _public_keyword_rule(item)
            for item in _settings_list(settings, "keyword_rules")
            if _normalize_keyword_rule(item)
        ],
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


@app.route("/api/mobile/events", methods=["GET"])
@device_or_login_required
def mobile_events():
    _ensure_mobile_event_monitor()
    since = _safe_int(request.args.get("since"), 0)
    settings = _read_viewer_settings()
    backlog = [event for event in _settings_list(settings, "sync_events") if _safe_int(event.get("seq"), 0) > since]

    def stream():
        subscriber: queue.Queue = queue.Queue(maxsize=100)
        with _MOBILE_EVENT_LOCK:
            _MOBILE_EVENT_SUBSCRIBERS.add(subscriber)
        try:
            yield ": connected\n\n"
            for event in backlog[-100:]:
                yield f"id: {_safe_int(event.get('seq'), 0)}\n"
                yield "event: sync\n"
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=25)
                    yield f"id: {_safe_int(event.get('seq'), 0)}\n"
                    yield "event: sync\n"
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _MOBILE_EVENT_LOCK:
                _MOBILE_EVENT_SUBSCRIBERS.discard(subscriber)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(stream()), headers=headers, mimetype="text/event-stream")


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


@app.route("/api/portable/export", methods=["GET"])
@login_required
def portable_export():
    settings = _read_viewer_settings()
    message_limit = _portable_parse_message_limit()
    include_messages = request.args.get("messages", "1").strip().lower() not in {"0", "false", "no"}
    selected_refs = _portable_query_account_refs()
    has_group_filter = "group" in request.args
    group_filter = _normalize_account_group(request.args.get("group", ""))
    has_account_filter = _portable_export_has_filter(selected_refs, has_group_filter)
    accounts = []
    folders = []
    messages = []
    exported_account_refs = set()
    export_stats = {
        "messagesIncluded": include_messages,
        "messageLimitPerMailbox": "all" if message_limit is None else message_limit,
        "group": group_filter if has_group_filter else "",
        "selectedAccounts": sorted(selected_refs),
        "truncated": False,
        "accounts": [],
        "importedMessages": 0,
    }

    mailboxes = settings.get("mailboxes", []) if isinstance(settings.get("mailboxes"), list) else []
    local_accounts = []
    for mailbox in mailboxes:
        account = _portable_account_from_local_mailbox(mailbox)
        if not account:
            continue
        if not _portable_account_selected(account, selected_refs, has_group_filter, group_filter):
            continue
        local_accounts.append(account)
        accounts.append(account)
        exported_account_refs.add(_portable_account_ref(account))
        folders.extend([
            _portable_folder(account, "inbox", "收件箱"),
            _portable_folder(account, "all", "所有邮件"),
            _portable_folder(account, "unread", "未读邮件"),
            _portable_folder(account, "sent", "已发送"),
            _portable_folder(account, "drafts", "草稿箱"),
            _portable_folder(account, "outbox", "发送失败"),
            _portable_folder(account, "trash", "回收站"),
        ])

    external_accounts = []
    normalized_external_accounts = []
    try:
        resp = http_session.get(urljoin(IMAP_MAIL_BASE_URL.rstrip("/") + "/", "api/accounts"), timeout=10)
        if resp.status_code == 200:
            external_accounts = resp.json() if isinstance(resp.json(), list) else []
    except Exception as exc:
        app.logger.warning("便携导出读取外部账号失败: %s", exc)
    for item in external_accounts:
        account = _portable_account_from_external(item)
        if not account:
            continue
        if not _portable_account_selected(account, selected_refs, has_group_filter, group_filter):
            continue
        normalized_external_accounts.append(account)
        accounts.append(account)
        exported_account_refs.add(_portable_account_ref(account))
        account_folders = _portable_external_folders(account)
        sync_status = item.get("syncStatus") or {}
        for folder in account_folders:
            if folder.get("key") == "__memail_all__" and not folder.get("count"):
                folder["count"] = _safe_int(sync_status.get("messages"), 0)
            elif folder.get("key") == "__memail_unread__" and not folder.get("count"):
                folder["count"] = _safe_int(sync_status.get("unseen"), 0)
        folders.extend(account_folders)

    imported_external_accounts = [
        item for item in _settings_list(settings, "portable_external_accounts")
        if isinstance(item, dict)
    ]
    imported_folders = [
        item for item in _settings_list(settings, "portable_folders")
        if isinstance(item, dict)
    ]
    imported_messages = [
        item for item in _settings_list(settings, "portable_messages")
        if isinstance(item, dict)
    ]
    existing_account_refs = {
        f"{account.get('type')}:{account.get('id')}".lower()
        for account in accounts
    }
    for account in imported_external_accounts:
        account = _portable_sanitize_account_profile(account)
        if not _portable_account_selected(account, selected_refs, has_group_filter, group_filter):
            continue
        ref = f"{account.get('type')}:{account.get('id')}".lower()
        if ref not in existing_account_refs:
            accounts.append(account)
            existing_account_refs.add(ref)
            exported_account_refs.add(ref)
    folder_keys = {_portable_folder_key(item) for item in folders}
    for folder in imported_folders:
        key = _portable_folder_key(folder)
        if has_account_filter and _portable_item_account_ref(folder) not in exported_account_refs:
            continue
        if key and key not in folder_keys:
            folders.append(folder)
            folder_keys.add(key)

    if include_messages and message_limit != 0:
        message_keys = set()

        def add_message(item: dict) -> bool:
            key = _portable_message_key(item)
            if not key or key in message_keys:
                return False
            messages.append(item)
            message_keys.add(key)
            return True

        for account in local_accounts:
            account_messages, counts = _portable_collect_local_messages(settings, account, message_limit)
            for key in ("inbox", "all", "unread", "sent", "drafts", "outbox", "trash"):
                _portable_set_folder_count(folders, account, key, counts.get(key, 0))
            for item in account_messages:
                add_message(item)
            export_stats["accounts"].append({
                "type": "local",
                "id": account.get("id", ""),
                "address": account.get("address", ""),
                "messages": len(account_messages),
                "total": counts.get("all", len(account_messages)),
                "truncated": bool(counts.get("truncated")),
            })
            export_stats["truncated"] = export_stats["truncated"] or bool(counts.get("truncated"))

        for account in normalized_external_accounts:
            try:
                account_messages, counts = _portable_collect_external_messages(settings, account, message_limit)
            except Exception as exc:
                app.logger.warning("便携导出读取外部邮件缓存失败: %s %s", account.get("address"), exc)
                account_messages, counts = [], {"__memail_all__": 0, "__memail_unread__": 0, "truncated": False}
            exported_folder_counts = {}
            for item in account_messages:
                folder_key = str(item.get("folder") or "INBOX")
                exported_folder_counts[folder_key] = exported_folder_counts.get(folder_key, 0) + 1
                _portable_append_folder(folders, folder_keys, _portable_folder(account, folder_key, folder_key))
                add_message(item)
            _portable_set_folder_count(folders, account, "__memail_all__", counts.get("__memail_all__", len(account_messages)))
            _portable_set_folder_count(folders, account, "__memail_unread__", counts.get("__memail_unread__", 0))
            for folder_key, count in exported_folder_counts.items():
                current_count = _portable_get_folder_count(folders, account, folder_key)
                if count > current_count:
                    _portable_set_folder_count(folders, account, folder_key, count)
            export_stats["accounts"].append({
                "type": "external",
                "id": account.get("id", ""),
                "address": account.get("address", ""),
                "messages": len(account_messages),
                "total": counts.get("__memail_all__", len(account_messages)),
                "truncated": bool(counts.get("truncated")),
            })
            export_stats["truncated"] = export_stats["truncated"] or bool(counts.get("truncated"))

        imported_added = 0
        for item in imported_messages:
            if has_account_filter and _portable_item_account_ref(item) not in exported_account_refs:
                continue
            if message_limit is not None and imported_added >= message_limit:
                export_stats["truncated"] = True
                break
            if add_message(item):
                imported_added += 1
        export_stats["importedMessages"] = imported_added

    exported_account_groups = {
        _normalize_account_group(account.get("group", ""))
        for account in accounts
        if isinstance(account, dict)
    }
    package = {
        "format": "memail.portable",
        "version": 1,
        "source": "server",
        "exportedAt": _iso_now(),
        "summary": {
            "accountCount": len(accounts),
            "folderCount": len(folders),
            "messageCount": len(messages),
            **export_stats,
        },
        "accounts": accounts,
        "folders": folders,
        "messages": messages,
        "aiSettings": _public_ai_settings_for_portable(),
        "translationSettings": _public_translation_settings_for_portable(),
        "keywordRules": [
            _portable_keyword_rule(item)
            for item in _settings_list(settings, "keyword_rules")
            if _portable_keyword_rule_matches_export(
                item,
                has_account_filter,
                exported_account_refs,
                exported_account_groups,
            )
        ],
    }
    return jsonify(package)


@app.route("/api/portable/import", methods=["POST"])
@login_required
def portable_import():
    data = request.get_json(silent=True) or {}
    if data.get("format") != "memail.portable":
        return jsonify({"success": False, "message": "不是 Memail 便携数据包"}), 400
    if _safe_int(data.get("version"), 1) > 1:
        return jsonify({"success": False, "message": "暂不支持该便携包版本"}), 400

    settings = _read_viewer_settings()
    imported = {
        "local_accounts": 0,
        "external_account_profiles": 0,
        "folders": 0,
        "messages": 0,
        "message_meta": 0,
        "keyword_rules": 0,
        "ai_channels": 0,
        "translation_settings": 0,
    }
    mailboxes = settings.get("mailboxes", []) if isinstance(settings.get("mailboxes"), list) else []
    by_address = {
        _normalize_mailbox_address(item.get("address", "")): item
        for item in mailboxes
        if isinstance(item, dict) and _normalize_mailbox_address(item.get("address", ""))
    }

    for account in data.get("accounts", []):
        if not isinstance(account, dict):
            continue
        account_type = str(account.get("type") or "").strip().lower()
        if account_type == "local":
            address = _normalize_mailbox_address(account.get("address", ""))
            if not address:
                continue
            item = by_address.get(address) or {"address": address, "created_at": _iso_now()}
            item.update({
                "display_name": _normalize_display_name(account.get("displayName") or account.get("display_name"), address),
                "send_name": _normalize_display_name(account.get("sendName") or account.get("send_name"), ""),
                "group": _normalize_account_group(account.get("group", "")),
                "updated_at": _iso_now(),
            })
            by_address[address] = item
            imported["local_accounts"] += 1
        elif account_type == "external":
            external_profiles = _settings_list(settings, "portable_external_accounts")
            external_profiles = [item for item in external_profiles if isinstance(item, dict)]
            normalized = _portable_sanitize_account_profile(account)
            ref = f"external:{normalized.get('id') or normalized.get('address')}".lower()
            external_profiles = [
                item for item in external_profiles
                if f"external:{item.get('id') or item.get('address')}".lower() != ref
            ]
            external_profiles.append(normalized)
            settings["portable_external_accounts"] = external_profiles
            imported["external_account_profiles"] += 1

    settings["mailboxes"] = list(by_address.values())
    order = _normalize_account_order(settings.get("account_order", []))
    for address in by_address:
        key = f"local:{address}"
        if key not in order:
            order.append(key)
    settings["account_order"] = order

    ai_settings = _normalize_portable_ai_settings(data.get("aiSettings") or data.get("ai_settings") or {})
    imported["ai_channels"] = _merge_portable_ai_settings(settings, ai_settings)

    translation_settings = _normalize_portable_translation_settings(
        data.get("translationSettings") or data.get("translation_settings") or {}
    )
    if translation_settings:
        _write_translation_settings(translation_settings)
        settings = _read_viewer_settings()
        imported["translation_settings"] = 1

    rules = [
        _normalize_keyword_rule(item)
        for item in data.get("keywordRules", data.get("keyword_rules", []))
        if isinstance(item, dict)
    ]
    rules = [item for item in rules if item]
    if rules:
        settings["keyword_rules"] = rules
        imported["keyword_rules"] = len(rules)

    folder_items = [
        item for item in data.get("folders", [])
        if isinstance(item, dict) and _portable_folder_key(item)
    ]
    if folder_items:
        stored = [
            item for item in _settings_list(settings, "portable_folders")
            if isinstance(item, dict)
        ]
        by_key = {_portable_folder_key(item): item for item in stored if _portable_folder_key(item)}
        for item in folder_items:
            by_key[_portable_folder_key(item)] = item
        settings["portable_folders"] = list(by_key.values())
        imported["folders"] = len(folder_items)

    message_items = [
        item for item in data.get("messages", [])
        if isinstance(item, dict) and _portable_message_key(item)
    ]
    if message_items:
        stored = [
            item for item in _settings_list(settings, "portable_messages")
            if isinstance(item, dict)
        ]
        by_key = {_portable_message_key(item): item for item in stored if _portable_message_key(item)}
        for item in message_items:
            by_key[_portable_message_key(item)] = item
        settings["portable_messages"] = list(by_key.values())
        imported["messages"] = len(message_items)
        meta_store = settings.get("message_meta", {})
        if not isinstance(meta_store, dict):
            meta_store = {}
        for item in message_items:
            raw_meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            favorite = bool(raw_meta.get("favorite", item.get("favorite", False)))
            pinned = bool(raw_meta.get("pinned", item.get("pinned", False)))
            color = str(raw_meta.get("color") or item.get("color") or "").strip().lower()
            color = color if re.fullmatch(r"(red|orange|yellow|green|blue|purple|gray)", color) else ""
            if not any([favorite, pinned, color]):
                continue
            key = _message_meta_key(
                item.get("accountType") or item.get("account_type") or "local",
                item.get("accountId") or item.get("account_id") or "",
                item.get("folder") or "INBOX",
                item.get("id") or item.get("message_id") or "",
            )
            meta_store[key] = {
                "account_type": item.get("accountType") or item.get("account_type") or "local",
                "account_id": item.get("accountId") or item.get("account_id") or "",
                "folder": item.get("folder") or "INBOX",
                "message_id": str(item.get("id") or item.get("message_id") or ""),
                "favorite": favorite,
                "pinned": pinned,
                "color": color,
                "updated_at": raw_meta.get("updated_at") or item.get("updatedAt") or item.get("updated_at") or _iso_now(),
            }
            imported["message_meta"] += 1
        settings["message_meta"] = meta_store

    _append_audit(settings, "portable.imported", imported)
    _touch_sync_event(settings, "portable.imported", imported)
    _write_viewer_settings(settings)
    return jsonify({
        "success": True,
        "imported": imported,
        "message": "便携包已导入。外部账号密码/OAuth Token 不在便携包内，需要在账号设置中重新填写或重新授权。",
    })


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
    cc = data.get("cc", "").strip()
    bcc = data.get("bcc", "").strip()
    subject = data.get("subject", "").strip()
    html = data.get("html", "").strip()
    text = data.get("text", "").strip()
    reply_to = data.get("reply_to", "").strip()
    attachments = _normalize_send_attachments(data.get("attachments", []), keep_content=True)

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
    cc_list = [addr.strip() for addr in cc.split(",") if addr.strip()]
    bcc_list = [addr.strip() for addr in bcc.split(",") if addr.strip()]

    # 构造 Resend API 请求
    payload = {
        "from": sender,
        "to": to_list,
        "subject": subject,
    }
    if cc_list:
        payload["cc"] = cc_list
    if bcc_list:
        payload["bcc"] = bcc_list
    sanitized_html = _sanitize_email_html(html) if html else ""
    if html:
        payload["html"] = sanitized_html
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to
    if attachments:
        payload["attachments"] = [
            {
                "filename": item["filename"],
                "content": item["content"],
                "content_type": item["content_type"],
            }
            for item in attachments
        ]

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
                        "cc": cc_list,
                        "bcc": bcc_list,
                        "subject": subject,
                        "text": text,
                        "html": sanitized_html,
                        "attachments": [
                            {
                                "filename": item["filename"],
                                "content_type": item["content_type"],
                                "size": item["size"],
                            }
                            for item in attachments
                        ],
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
