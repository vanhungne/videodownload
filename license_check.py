# ==== license_check.py (nhúng public key, verify token) ====
from __future__ import annotations
import os, base64, pathlib, datetime, typing as _t
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

APP_SETTINGS_DIR = pathlib.Path(r"C:\11LABSV3\Settings")
APP_LICENSE_FILE = APP_SETTINGS_DIR / "license_token.txt"

# THAY public key của bạn vào đây (PEM, X.509 SubjectPublicKeyInfo)
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA8Sp6u0xiwQDdWlinmmbS
xvrjxmyYsIQf3IZjUg6BVrMTQTeY8dOlVmc+ro1d9/fOVt+TAklJv8WbQrjrU1pL
ACeWwPJoOXatzqDwZqXYzQmPnxOntOoeaDTh5IADUUK1q+rfeVNNByA6Hdg5+SQI
oU3LR/TT+GpSiKiYaCPBkGTd3Bax5lGs4eEsL+2wgbLvfOif9qEp0HbxYE9teB45
JyblSHCAaQD30YOzZm5hMkbOW8oGnGyZZe6KVT3AYo8xugORVu6YTfRrOty8FjDd
73pNTslBT25P725s/bPP305rp81+NIpXmuPzK4gZn8MVUt+A1KgwBGRhd/JHrDbj
DQIDAQAB
-----END PUBLIC KEY-----
"""

def get_device_id() -> str:
    """Đọc MachineGuid làm Device ID (phải khớp với token)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
            v, _ = winreg.QueryValueEx(k, "MachineGuid")
        return str(v).strip()
    except Exception:
        return ""

def _load_public_key():
    return serialization.load_pem_public_key(PUBLIC_KEY_PEM)

def _read_token_file() -> str:
    if not APP_LICENSE_FILE.exists():
        return ""
    try:
        return APP_LICENSE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def _parse_token(token: str) -> tuple[str, str, datetime.date, bytes]:
    # token: DID|OWNER|YYYY-MM-DD|BASE64_SIG
    parts = (token or "").split("|")
    if len(parts) != 4:
        raise ValueError("Sai định dạng token")
    did, owner, exp_s, sig_b64 = parts
    exp_date = datetime.date.fromisoformat(exp_s)  # sẽ raise nếu sai định dạng
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except Exception:
        raise ValueError("Chữ ký không phải base64 hợp lệ")
    return did.strip(), owner.strip(), exp_date, sig

def _verify_signature(did: str, owner: str, exp: datetime.date, sig: bytes) -> bool:
    message = f"{did}|{owner}|{exp.isoformat()}".encode("utf-8")
    pk = _load_public_key()
    try:
        pk.verify(sig, message, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False

class LicenseStatus(_t.NamedTuple):
    ok: bool
    reason: str
    owner: str
    exp: _t.Optional[datetime.date]

def check_license() -> LicenseStatus:
    """Đọc file token, verify chữ ký, kiểm tra hạn & device."""
    token = _read_token_file()
    if not token:
        return LicenseStatus(False, "Không tìm thấy license_token.txt", "", None)

    try:
        did_tkn, owner, exp, sig = _parse_token(token)
    except Exception as e:
        return LicenseStatus(False, f"Token lỗi: {e}", "", None)

    did_local = get_device_id()
    if not did_local:
        return LicenseStatus(False, "Không lấy được Device ID (MachineGuid).", owner, exp)
    if did_tkn.strip().lower() != did_local.strip().lower():
        return LicenseStatus(False, "Device ID không khớp.", owner, exp)

    if not _verify_signature(did_tkn, owner, exp, sig):
        return LicenseStatus(False, "Chữ ký không hợp lệ.", owner, exp)

    today = datetime.date.today()
    if exp < today:
        return LicenseStatus(False, "License đã hết hạn.", owner, exp)

    return LicenseStatus(True, "OK", owner, exp)

def save_token_text(text: str) -> None:
    APP_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    APP_LICENSE_FILE.write_text(text.strip(), encoding="utf-8")
