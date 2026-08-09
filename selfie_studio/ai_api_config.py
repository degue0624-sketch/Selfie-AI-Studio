import base64
import ctypes
import ctypes.wintypes
import json
import os
from pathlib import Path


DEFAULTS = {
    "enabled": False,
    "provider": "OpenAI",
    "model": "gpt-5.6-luna",
    "base_url": "https://api.openai.com/v1",
    "api_key_protected": "",
}


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(data):
    raw = bytes(data or b"")
    if not raw:
        return DATA_BLOB(0, None), None
    buffer = ctypes.create_string_buffer(raw)
    blob = DATA_BLOB(
        len(raw),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def _protect_windows(text):
    if not text:
        return ""
    if os.name != "nt":
        return ""

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    src, src_buf = _blob_from_bytes(text.encode("utf-8"))
    dst = DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(src),
        "Selfie AI Studio API Key",
        None,
        None,
        None,
        0,
        ctypes.byref(dst),
    )
    if not ok:
        raise OSError("Windows DPAPIでAPIキーを暗号化できませんでした。")

    try:
        protected = ctypes.string_at(dst.pbData, dst.cbData)
        return base64.b64encode(protected).decode("ascii")
    finally:
        if dst.pbData:
            kernel32.LocalFree(dst.pbData)


def _unprotect_windows(encoded):
    if not encoded:
        return ""
    if os.name != "nt":
        return ""

    raw = base64.b64decode(encoded.encode("ascii"))
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    src, src_buf = _blob_from_bytes(raw)
    dst = DATA_BLOB()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(src),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(dst),
    )
    if not ok:
        return ""

    try:
        clear = ctypes.string_at(dst.pbData, dst.cbData)
        return clear.decode("utf-8")
    finally:
        if dst.pbData:
            kernel32.LocalFree(dst.pbData)


def load_api_config(path):
    path = Path(path)
    result = dict(DEFAULTS)

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        if isinstance(data, dict):
            for key in DEFAULTS:
                if key in data:
                    result[key] = data[key]

            # Migration: old Step50-0 plaintext key is read once and migrated
            # when the settings are next saved.
            legacy_key = str(data.get("api_key") or "")
            if legacy_key and not result.get("api_key_protected"):
                result["_legacy_api_key"] = legacy_key

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        result["_api_key"] = env_key
        result["_key_source"] = "環境変数"
    else:
        protected = str(result.get("api_key_protected") or "")
        key = _unprotect_windows(protected) if protected else ""
        if not key:
            key = str(result.get("_legacy_api_key") or "")
        result["_api_key"] = key
        result["_key_source"] = "Windows暗号化保存" if protected and key else (
            "旧設定" if key else "未設定"
        )

    return result


def save_api_config(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    api_key = str((data or {}).get("api_key") or "").strip()

    payload = dict(DEFAULTS)
    payload.update({
        "enabled": bool((data or {}).get("enabled", False)),
        "provider": str((data or {}).get("provider") or "OpenAI"),
        "model": str((data or {}).get("model") or "gpt-5.6-luna"),
        "base_url": str(
            (data or {}).get("base_url")
            or "https://api.openai.com/v1"
        ).rstrip("/"),
        "api_key_protected": _protect_windows(api_key) if api_key else "",
    })

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def masked_key(value):
    value = str(value or "")
    if not value:
        return "未設定"
    if len(value) <= 8:
        return "********"
    return value[:4] + "…" + value[-4:]


def effective_api_key(config=None, ui_value=""):
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key, "環境変数"

    ui_value = str(ui_value or "").strip()
    if ui_value:
        return ui_value, "Studio設定"

    config = config or {}
    key = str(config.get("_api_key") or "").strip()
    return key, str(config.get("_key_source") or "未設定")
