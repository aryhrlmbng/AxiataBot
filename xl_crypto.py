"""
Crypto & signing layer for the myXL API — 100% lokal, tanpa service pihak ketiga.

Kunci & algoritma di-reverse-engineer dari aplikasi myXL Android v8.6.0.
Referensi implementasi: elite-x1x/exel (app/service/service_git.py), Banday-Wrt/myxl-cli.
"""
import hashlib
import os
import hmac
import base64

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# --- Kunci statis hasil reverse engineering (boleh dioverride via env) ---
XDATA_KEY = os.getenv("XL_XDATA_KEY", "5dccbf08920a5527b99e222789c34bb7")
AX_API_SIG_KEY = os.getenv("XL_AX_API_SIG_KEY", "18b4d589826af50241177961590e6693")
X_API_BASE_SECRET = os.getenv(
    "XL_X_API_BASE_SECRET",
    "mU1Y4n1vBjf3M7tMnRkFU08mVyUJHed8B5En3EAniu1mXLixeuASmBmKnkyzVziOye7rG5nIekMdthensbQMcOJ6SLnrkGyfXALD7mrBC6vuWv6G01pmD3XlU5rT7Tzx",
)
ENCRYPTED_FIELD_KEY = os.getenv("XL_ENCRYPTED_FIELD_KEY", "5dccbf08920a5527")

API_KEY = "vT8tINqHaOxXbGE7eOWAhA=="  # x-api-key statis


# --------------------------------------------------------------------------
# AES helpers
# --------------------------------------------------------------------------

def derive_iv(xtime_ms: int) -> bytes:
    """IV diturunkan dari timestamp: SHA256(str(xtime_ms))[:16]."""
    sha = hashlib.sha256(str(xtime_ms).encode()).hexdigest()
    return sha[:16].encode()


def encrypt_xdata(plaintext: str, xtime_ms: int) -> str:
    """Enkripsi body request -> xdata (AES-256-CBC, PKCS7, urlsafe base64)."""
    iv = derive_iv(xtime_ms)
    key_bytes = XDATA_KEY.encode()
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    return base64.urlsafe_b64encode(
        cipher.encrypt(pad(plaintext.encode(), 16, style="pkcs7"))
    ).decode()


def decrypt_xdata(xdata: str, xtime_ms: int) -> str:
    """Dekripsi xdata dari response -> plaintext JSON."""
    iv = derive_iv(xtime_ms)
    key_bytes = XDATA_KEY.encode()
    ct = base64.urlsafe_b64decode(xdata + "=" * ((4 - len(xdata) % 4) % 4))
    pt = AES.new(key_bytes, AES.MODE_CBC, iv).decrypt(ct)
    return unpad(pt, 16, style="pkcs7").decode()


def build_encrypted_field(iv_hex16: str | None = None, urlsafe_b64: bool = False) -> str:
    """Field kosong terenkripsi (dipakai payment payload). Pola app: AES-CBC
    dengan key 16 byte ENCRYPTED_FIELD_KEY dan IV = ASCII dari 16-hex-char string,
    lalu base64(ct) + iv_hex."""
    key = ENCRYPTED_FIELD_KEY.encode("ascii")
    iv_hex = iv_hex16 or os.urandom(8).hex()
    iv = iv_hex.encode("ascii")
    ct = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(pad(b"", AES.block_size))
    b64 = base64.urlsafe_b64encode if urlsafe_b64 else base64.b64encode
    return b64(ct).decode("ascii") + iv_hex


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------

def make_x_signature(id_token: str, method: str, path: str, sig_time_sec: int) -> str:
    """x-signature untuk request API umum."""
    key_str = f"{X_API_BASE_SECRET};{id_token};{method};{path};{sig_time_sec}"
    key_bytes = key_str.encode("utf-8")
    msg = f"{id_token};{sig_time_sec};".encode("utf-8")
    return hmac.new(key_bytes, msg, hashlib.sha512).hexdigest()


def make_x_signature_basic(method: str, path: str, sig_time_sec: int) -> str:
    """x-signature untuk endpoint tanpa id_token (mis. cek nomor)."""
    key_str = f"{X_API_BASE_SECRET};{method};{path};{sig_time_sec}"
    key_bytes = key_str.encode("utf-8")
    msg = f"{sig_time_sec};en;".encode("utf-8")
    return hmac.new(key_bytes, msg, hashlib.sha512).hexdigest()


def make_x_signature_payment(
    access_token: str,
    sig_time_sec: int,
    package_code: str,
    token_payment: str,
    payment_method: str,
    payment_for: str,
    path: str,
) -> str:
    """x-signature untuk transaksi pembayaran."""
    key_str = f"{X_API_BASE_SECRET};{sig_time_sec}#ae-hei_9Tee6he+Ik3Gais5=;POST;{path};{sig_time_sec}"
    key_bytes = key_str.encode("utf-8")
    msg = f"{access_token};{token_payment};{sig_time_sec};{payment_for};{payment_method};{package_code};".encode(
        "utf-8"
    )
    return hmac.new(key_bytes, msg, hashlib.sha512).hexdigest()


def make_x_signature_bounty(
    access_token: str,
    sig_time_sec: int,
    package_code: str,
    token_payment: str,
    payment_method: str = "",
) -> str:
    """x-signature untuk bonus/bounty redemption."""
    key_str = f"{X_API_BASE_SECRET};{sig_time_sec}#ae-hei_9Tee6he+Ik3Gais5=;{payment_method};{sig_time_sec}"
    key_bytes = key_str.encode("utf-8")
    msg = f"{access_token};{token_payment};{sig_time_sec};{package_code};".encode("utf-8")
    return hmac.new(key_bytes, msg, hashlib.sha512).hexdigest()


def make_x_signature_loyalty(
    access_token: str,
    sig_time_sec: int,
    package_code: str,
    token_payment: str,
) -> str:
    """x-signature untuk loyalty/bintang redemption."""
    key_str = f"{X_API_BASE_SECRET};{sig_time_sec}#ae-hei_9Tee6he+Ik3Gais5=;POST;{sig_time_sec}"
    key_bytes = key_str.encode("utf-8")
    msg = f"{access_token};{token_payment};{sig_time_sec};LOYALTY_REDEMPTION;;{package_code};".encode("utf-8")
    return hmac.new(key_bytes, msg, hashlib.sha512).hexdigest()


def make_ax_api_signature(ts_for_sign: str, contact: str, code: str, contact_type: str) -> str:
    """Ax-Api-Signature untuk submit OTP di CIAM."""
    key_bytes = AX_API_SIG_KEY.encode("ascii")
    preimage = f"{ts_for_sign}password{contact_type}{contact}{code}openid"
    digest = hmac.new(key_bytes, preimage.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def ts_gmt7_without_colon(dt) -> str:
    """Format timestamp GMT+7 tanpa colon, dipakai header request (mis. 2024-01-01T12:00:00.123+0700)."""
    if dt.tzinfo is None:
        from datetime import timezone, timedelta

        dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))
    millis = f"{int(dt.microsecond / 1000):03d}"
    tz = dt.strftime("%z")
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{millis}") + tz


def java_like_timestamp(dt) -> str:
    """Format 2024-01-01T12:00:00.12+07:00 (dipakai Ax-Request-At)."""
    ms2 = f"{int(dt.microsecond / 10000):02d}"
    tz = dt.strftime("%z")
    tz_colon = tz[:-2] + ":" + tz[-2:] if tz else "+00:00"
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{ms2}") + tz_colon
