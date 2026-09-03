"""
XL Axiata API client — OTP login, profile, balance, quota, packages, purchase.

Semua request ke myXL API dienkripsi & ditandatangani secara lokal.
"""
import os
import base64
import json
import time
import uuid
import hashlib
import logging
import brotli
import zlib

import requests
from datetime import datetime, timezone, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from xl_crypto import (
    encrypt_xdata,
    decrypt_xdata,
    make_x_signature,
    make_x_signature_payment,
    make_x_signature_basic,
    make_ax_api_signature,
    java_like_timestamp,
    ts_gmt7_without_colon,
    API_KEY,
)

log = logging.getLogger(__name__)


def decode_response(resp) -> str:
    """Decode body response mengikuti Content-Encoding (brotli/gzip)."""
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if enc == "br":
        try:
            return brotli.decompress(resp.content).decode("utf-8")
        except Exception:
            # Kadang urllib3 sudah auto-decompress — pakai content mentah
            return resp.content.decode("utf-8", errors="replace")
    if enc == "gzip":
        try:
            return zlib.decompress(resp.content, zlib.MAX_WBITS | 16).decode("utf-8")
        except Exception:
            return resp.content.decode("utf-8", errors="replace")
    return resp.text


# --- Constants ---
BASE_URL = "https://api.myxl.xlaxiata.co.id"
CIAM_BASE = "https://gede.ciam.xlaxiata.co.id/realms/xl-ciam"

# Static device headers (dari reverse engineering myXL Android app)
AX_FP_KEY = os.getenv("XL_AX_FP_KEY", "18b4d589826af50241177961590e6693")


def _build_fingerprint() -> str:
    """Generate fingerprint ala myXL app: AES-CBC(dev_info, key=AX_FP_KEY, iv=0x00*16)."""
    dev_str = "samsung9999|SM-N939999|en|720x1540|GMT07:00|192.169.69.69|1.0|Android 13|6281398370564"
    key = AX_FP_KEY.encode("ascii")
    iv = b"\x00" * 16
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(dev_str.encode(), 16))
    return base64.b64encode(ct).decode()


# Device identity — token CIAM terikat ke (device_id, fingerprint) saat login.
# Nilai static ini proven bekerja untuk login & refresh (dari bot-tele-xl reference).
# Boleh dioverride via env kalau butuh identity baru.
AX_FINGERPRINT = os.getenv(
    "XL_AX_FINGERPRINT",
    "YmQLy9ZiLLBFAEVcI4Dnw9+NJWZcdGoQyewxMF/9hbfk/8GbKBgtZxqdiiam8+m2lK31E/zJQ7kjuPXpB3EE8naYL0Q8+0WLhFV1WAPl9Eg=",
)
AX_DEVICE_ID = os.getenv("XL_AX_DEVICE_ID", "92fb44c0804233eb4d9e29f838223a14")

DEVICE_HEADERS = {
    "Ax-Device-Id": AX_DEVICE_ID,
    "Ax-Fingerprint": AX_FINGERPRINT,
    "Ax-Request-Device": "samsung",
    "Ax-Request-Device-Model": "SM-N935F",
    "Ax-Request-Id": lambda: str(uuid.uuid4()),
    "Ax-Substype": "PREPAID",
    # CIAM (login/refresh) terikat versi app saat sesi dibuat — 8.6.0 proven jalan
    "User-Agent": "myXL / 8.6.0(1179); com.android.vending; (samsung; SM-N935F; SDK 33; Android 13)",
}

# Business API (api.myxl) butuh versi lebih baru
APP_VERSION = "8.10.0"
API_UA = "myXL / 8.9.1(1204); com.android.vending; (samsung; SM-N935F; SDK 33; Android 13)"

CIAM_CLIENT_ID = "9fc97ed1-6a30-48d5-9516-60c53ce3a135"
CIAM_CLIENT_SECRET = "YDWmF4LJj9XIKwQnzy2e2lb0tJQb29o3"
CIAM_BASIC_AUTH = "Basic " + base64.b64encode(f"{CIAM_CLIENT_ID}:{CIAM_CLIENT_SECRET}".encode()).decode()


# --------------------------------------------------------------------------
# Auth (CIAM – Keycloak XL)
# --------------------------------------------------------------------------

def validate_contact(contact: str) -> str | None:
    """Normalisasi & validasi nomor HP: harus 628... maks 14 digit."""
    contact = contact.strip()
    if not contact.startswith("628") or len(contact) > 14 or not contact.isdigit():
        return None
    return contact


def get_otp(contact: str) -> str | None:
    """Minta OTP via SMS. Return subscriber_id (perlu untuk submit OTP)."""
    contact = validate_contact(contact)
    if not contact:
        return None

    now = datetime.now(timezone(timedelta(hours=7)))
    ax_request_at = java_like_timestamp(now)
    ax_request_id = str(uuid.uuid4())

    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": CIAM_BASIC_AUTH,
        **DEVICE_HEADERS,
        "Ax-Request-At": ax_request_at,
        "Ax-Request-Id": ax_request_id,
        "Content-Type": "application/json",
        "Host": "gede.ciam.xlaxiata.co.id",
    }

    params = {"contact": contact, "contactType": "SMS", "alternateContact": "false"}

    try:
        resp = requests.get(
            f"{CIAM_BASE}/auth/otp",
            params=params,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        subscriber_id = data.get("subscriber_id")
        if not subscriber_id:
            log.error("get_otp: no subscriber_id in response: %s", data)
            return None
        return subscriber_id
    except requests.RequestException as e:
        log.error("get_otp error: %s", e)
        return None


def submit_otp(contact: str, code: str, subscriber_id: str | None = None) -> dict | None:
    """Submit OTP ke CIAM. Return dict {access_token, refresh_token, id_token}."""
    contact = validate_contact(contact)
    if not contact or len(code) != 6:
        return None

    now = datetime.now(timezone(timedelta(hours=7)))
    ts_for_sign = ts_gmt7_without_colon(now)
    ts_header = ts_gmt7_without_colon(now - timedelta(minutes=5))  # app sends 5m ke belakang
    ax_request_id = str(uuid.uuid4())

    signature = make_ax_api_signature(ts_for_sign, contact, code, "SMS")

    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": CIAM_BASIC_AUTH,
        "Ax-Api-Signature": signature,
        "Ax-Device-Id": DEVICE_HEADERS["Ax-Device-Id"],
        "Ax-Fingerprint": DEVICE_HEADERS["Ax-Fingerprint"],
        "Ax-Request-At": ts_header,
        "Ax-Request-Device": DEVICE_HEADERS["Ax-Request-Device"],
        "Ax-Request-Device-Model": DEVICE_HEADERS["Ax-Request-Device-Model"],
        "Ax-Request-Id": ax_request_id,
        "Ax-Substype": "PREPAID",
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "gede.ciam.xlaxiata.co.id",
        "User-Agent": DEVICE_HEADERS["User-Agent"],
    }

    payload = {
        "contactType": "SMS",
        "code": code,
        "grant_type": "password",
        "contact": contact,
        "scope": "openid",
    }

    try:
        resp = requests.post(
            f"{CIAM_BASE}/protocol/openid-connect/token",
            data=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "id_token": data.get("id_token"),
            "expires_in": data.get("expires_in"),
        }
    except requests.RequestException as e:
        log.error("submit_otp error: %s", e)
        log.debug("Response: %s", getattr(e.response, "text", "N/A"))
        return None


def refresh_token(refresh_token: str) -> dict | None:
    """Refresh token yang expired."""
    now = datetime.now(timezone(timedelta(hours=7)))
    ax_request_at = ts_gmt7_without_colon(now)
    ax_request_id = str(uuid.uuid4())

    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Authorization": CIAM_BASIC_AUTH,
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "gede.ciam.xlaxiata.co.id",
        "Ax-Device-Id": AX_DEVICE_ID,
        "Ax-Fingerprint": AX_FINGERPRINT,
        "Ax-Request-At": ax_request_at,
        "Ax-Request-Device": "samsung",
        "Ax-Request-Device-Model": "SM-N935F",
        "Ax-Request-Id": ax_request_id,
        "Ax-Substype": "PREPAID",
        "User-Agent": DEVICE_HEADERS["User-Agent"],
    }

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    try:
        resp = requests.post(
            f"{CIAM_BASE}/protocol/openid-connect/token",
            data=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token") or refresh_token,
            "id_token": data.get("id_token"),
            "expires_in": data.get("expires_in"),
        }
    except requests.RequestException as e:
        log.error("refresh_token error: %s", e)
        log.debug("Response: %s", getattr(e.response, "text", "N/A"))
        return None


# --------------------------------------------------------------------------
# Encrypted API request
# --------------------------------------------------------------------------

def send_api_request(
    id_token: str,
    method: str,
    path: str,
    body: dict | None = None,
    xtime_ms: int | None = None,
    x_signature_override: str | None = None,
) -> dict | None:
    """Kirim request terenkripsi ke XL API, terima response terdekripsi.
    x_signature_override: untuk payment endpoint yang butuh signature berbeda.
    """
    if xtime_ms is None:
        xtime_ms = int(time.time() * 1000)
    sig_time_sec = xtime_ms // 1000
    plaintext = json.dumps(body or {}, separators=(",", ":"))

    # Enkripsi body
    xdata = encrypt_xdata(plaintext, xtime_ms)

    # Signature — override untuk payment, default untuk sisanya
    if x_signature_override:
        x_signature = x_signature_override
    else:
        x_signature = make_x_signature(id_token, method, path, sig_time_sec)

    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json; charset=utf-8",
        "Host": "api.myxl.xlaxiata.co.id",
        "x-api-key": API_KEY,
        "authorization": f"Bearer {id_token}",
        "x-hv": "v3",
        "x-signature": x_signature,
        "x-signature-time": str(sig_time_sec),
        "x-request-id": str(uuid.uuid4()),
        "x-request-at": java_like_timestamp(datetime.now(timezone.utc).astimezone()),
        "x-version-app": APP_VERSION,
        "User-Agent": API_UA,
    }

    payload = json.dumps({"xdata": xdata, "xtime": xtime_ms})

    try:
        resp = requests.request(
            method,
            f"{BASE_URL}/{path.lstrip('/')}",
            data=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        log.error("send_api_request %s %s error: %s", method, path, e)
        if hasattr(e, "response") and e.response is not None:
            log.debug("Response status: %s body: %s", e.response.status_code, e.response.text[:500])
        return None
    except json.JSONDecodeError:
        log.error("send_api_request: invalid JSON response for %s %s", method, path)
        return None

    # Decrypt response
    resp_xdata = raw.get("xdata")
    resp_xtime = raw.get("xtime")
    if resp_xdata and resp_xtime:
        try:
            decrypted = decrypt_xdata(resp_xdata, resp_xtime)
            return json.loads(decrypted)
        except (ValueError, json.JSONDecodeError) as e:
            log.error("send_api_request: decrypt/parse failed: %s", e)
            return raw  # fallback
    return raw


# --------------------------------------------------------------------------
# API Endpoints
# --------------------------------------------------------------------------

def get_profile(id_token: str, access_token: str = "") -> dict | None:
    """Profile pengguna."""
    return send_api_request(
        id_token, "POST", "api/v8/profile",
        body={
            "access_token": access_token,
            "app_version": APP_VERSION,
            "is_enterprise": False,
            "lang": "en",
        },
    )


def get_balance(id_token: str, access_token: str = "") -> dict | None:
    """Saldo + credit info."""
    return send_api_request(
        id_token, "POST", "api/v8/packages/balance-and-credit",
        body={"is_enterprise": False, "lang": "en", "access_token": access_token},
    )


def get_quota(id_token: str, access_token: str = "") -> dict | None:
    """Detail kuota (paket aktif)."""
    return send_api_request(
        id_token, "POST", "api/v8/packages/quota-details",
        body={"is_enterprise": False, "lang": "en", "access_token": access_token},
    )


def get_families(id_token: str) -> dict | None:
    """Daftar family code paket."""
    return send_api_request(
        id_token, "POST", "api/v8/xl-stores/families",
        body={"lang": "en", "is_enterprise": False},
    )


def intercept_page(id_token: str, option_code: str) -> dict | None:
    """Panggil intercept-page sebelum purchase (langkah wajib di app)."""
    return send_api_request(
        id_token, "POST", "misc/api/v8/utility/intercept-page",
        body={"is_enterprise": False, "lang": "en", "package_option_code": option_code},
    )


def get_options(id_token: str, family_code: str) -> dict | None:
    """Daftar paket dalam suatu family."""
    return send_api_request(
        id_token, "POST", "api/v8/xl-stores/options/list",
        body={"family_code": family_code, "lang": "en", "is_enterprise": False},
    )


def get_option_detail(
    id_token: str,
    package_option_code: str,
    package_family_code: str = "",
    package_variant_code: str = "",
) -> dict | None:
    """Detail satu paket (bawa token_confirmation untuk purchase)."""
    return send_api_request(
        id_token, "POST", "api/v8/xl-stores/options/detail",
        body={
            "is_transaction_routine": False,
            "migration_type": "NONE",
            "package_family_code": package_family_code,
            "family_role_hub": "",
            "is_autobuy": False,
            "is_enterprise": False,
            "is_shareable": False,
            "is_migration": False,
            "lang": "id",
            "package_option_code": package_option_code,
            "is_upsell_pdp": False,
            "package_variant_code": package_variant_code,
        },
    )


def get_payment_methods(id_token: str, payment_target: str, token_confirmation: str) -> dict | None:
    """Metode pembayaran yang tersedia."""
    return send_api_request(
        id_token, "POST", "payments/api/v8/payment-methods-option",
        body={
            "payment_type": "PURCHASE",
            "is_enterprise": False,
            "payment_target": payment_target,
            "lang": "en",
            "is_referral": False,
            "token_confirmation": token_confirmation,
        },
    )


def purchase_balance(
    id_token: str,
    access_token: str,
    option_code: str,
    price: int,
    token_confirmation: str,
    token_payment: str,
    ts_to_sign: int | None = None,
    xtime_ms: int | None = None,
) -> dict | None:
    """Beli paket via saldo (pulsa).

    Endpoint: payments/api/v8/settlement-multipayment dengan payment_method=BALANCE.
    ts_to_sign: server timestamp dari payment-methods-option response.
    """
    if ts_to_sign is None:
        ts_to_sign = int(time.time())
    if xtime_ms is None:
        xtime_ms = int(time.time() * 1000)

    path = "payments/api/v8/settlement-multipayment"
    x_signature = make_x_signature_payment(
        access_token, ts_to_sign, option_code, token_payment, "BALANCE", "BUY_PACKAGE", path
    )

    body = {
        "total_discount": 0,
        "is_enterprise": False,
        "payment_token": "",
        "token_payment": token_payment,
        "activated_autobuy_code": "",
        "cc_payment_type": "",
        "is_myxl_wallet": False,
        "pin": "",
        "ewallet_promo_id": "",
        "members": [],
        "total_fee": 0,
        "fingerprint": "",
        "autobuy_threshold_setting": {"label": "", "type": "", "value": 0},
        "is_use_point": False,
        "lang": "en",
        "payment_method": "BALANCE",
        "timestamp": int(time.time()),
        "points_gained": 0,
        "can_trigger_rating": False,
        "akrab_members": [],
        "akrab_parent_alias": "",
        "referral_unique_code": "",
        "coupon": "",
        "payment_for": "BUY_PACKAGE",
        "with_upsell": False,
        "topup_number": "",
        "stage_token": "",
        "authentication_id": "",
        "encrypted_payment_token": "",
        "token": "",
        "token_confirmation": token_confirmation,
        "access_token": access_token,
        "wallet_number": "",
        "encrypted_authentication_id": "",
        "additional_data": {},
        "total_amount": price,
        "is_using_autobuy": False,
        "items": [
            {
                "item_code": option_code,
                "product_type": "",
                "item_price": price,
                "item_name": "",
                "tax": 0,
            }
        ],
    }

    # Override signature di header (payment pakai format signature beda)
    resp = send_api_request(
        id_token, "POST", path, body=body, xtime_ms=xtime_ms, x_signature_override=x_signature
    )
    return resp


def purchase_ewallet(
    id_token: str,
    access_token: str,
    option_code: str,
    price: int,
    token_confirmation: str,
    token_payment: str,
    payment_method: str,
    wallet_number: str,
    xtime_ms: int | None = None,
) -> dict | None:
    """Beli paket via e-wallet (DANA/GOPAY/OVO/SHOPEEPAY)."""
    if xtime_ms is None:
        xtime_ms = int(time.time() * 1000)
    sig_time_sec = xtime_ms // 1000

    path = "payments/api/v8/settlement-multipayment/ewallet"
    x_signature = make_x_signature_payment(
        access_token, sig_time_sec, option_code, token_payment, payment_method.upper(), "BUY_PACKAGE", path
    )
    body = {
        "payment_type": "PURCHASE",
        "is_enterprise": False,
        "payment_target": option_code,
        "lang": "en",
        "is_referral": False,
        "with_upsell": False,
        "topup_number": "",
        "stage_token": "",
        "authentication_id": "",
        "token": "",
        "token_confirmation": token_confirmation,
        "access_token": access_token,
        "wallet_number": wallet_number,
        "additional_data": {},
        "total_amount": price,
        "is_using_autobuy": False,
        "items": [
            {
                "item_code": option_code,
                "product_type": "",
                "item_price": price,
                "item_name": "",
                "tax": 0,
            }
        ],
    }
    return send_api_request(
        id_token, "POST", path, body=body, xtime_ms=xtime_ms, x_signature_override=x_signature
    )


# --------------------------------------------------------------------------
# QRIS Purchase
# --------------------------------------------------------------------------

def purchase_qris(
    id_token: str,
    access_token: str,
    option_code: str,
    price: int,
    token_confirmation: str,
    token_payment: str,
    ts_to_sign: int,
    xtime_ms: int | None = None,
) -> dict | None:
    """Beli paket via QRIS. Return {transaction_code} on success.
    ts_to_sign: server timestamp dari payment-methods-option response.
    """
    if xtime_ms is None:
        xtime_ms = int(time.time() * 1000)
    sig_time_sec = xtime_ms // 1000

    path = "payments/api/v8/settlement-multipayment/qris"
    # Payment signature pakai ts_to_sign dari server
    x_signature = make_x_signature_payment(
        access_token, ts_to_sign, option_code, token_payment, "QRIS", "BUY_PACKAGE", path
    )
    body = {
        "akrab": {"akrab_members": [], "akrab_parent_alias": "", "members": []},
        "can_trigger_rating": False,
        "total_discount": 0,
        "coupon": "",
        "payment_for": "BUY_PACKAGE",
        "topup_number": "",
        "stage_token": "",
        "is_enterprise": False,
        "autobuy": {
            "is_using_autobuy": False,
            "activated_autobuy_code": "",
            "autobuy_threshold_setting": {"label": "", "type": "", "value": 0},
        },
        "access_token": access_token,
        "is_myxl_wallet": False,
        "additional_data": {
            "original_price": price,
            "is_spend_limit_temporary": False,
            "migration_type": "",
            "spend_limit_amount": 0,
            "is_spend_limit": False,
            "tax": 0,
            "benefit_type": "",
            "quota_bonus": 0,
            "cashtag": "",
            "is_family_plan": False,
            "combo_details": [],
            "is_switch_plan": False,
            "discount_recurring": 0,
            "has_bonus": False,
            "discount_promo": 0,
        },
        "total_amount": price,
        "total_fee": 0,
        "is_use_point": False,
        "lang": "en",
        "items": [
            {
                "item_code": option_code,
                "product_type": "",
                "item_price": price,
                "item_name": "",
                "tax": 0,
                "token_confirmation": token_confirmation,
            }
        ],
        "verification_token": token_payment,
        "payment_method": "QRIS",
        "timestamp": int(time.time()),
    }
    return send_api_request(
        id_token, "POST", path, body=body, xtime_ms=xtime_ms, x_signature_override=x_signature
    )


def get_qris_code(id_token: str, transaction_id: str) -> str | None:
    """Dapatkan QRIS string dari transaction_id."""
    r = send_api_request(
        id_token, "POST", "payments/api/v8/pending-detail",
        body={"transaction_id": transaction_id, "is_enterprise": False, "lang": "en", "status": ""},
    )
    if r and r.get("status") == "SUCCESS":
        return r.get("data", {}).get("qr_code")
    return None