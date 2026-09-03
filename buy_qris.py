"""
Beli paket XL via QRIS — full flow.
Usage: python buy_qris.py <cari_keyword> [limit_harga]
Contoh: python buy_qris.py "Flex 13" 50000
"""
import json
import os
import sys
import time
import base64
import logging

import qrcode

import xl_api

logging.basicConfig(level=logging.ERROR)
log = logging.getLogger("buy_qris")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "last_tokens.json")


def load_tokens() -> dict:
    with open(DATA_FILE) as f:
        return json.load(f)


def save_tokens(d: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)


def ensure_token() -> dict:
    d = load_tokens()
    # Refresh jika perlu (token access valid 10 menit)
    import base64 as b64

    try:
        payload = d["access_token"].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(b64.urlsafe_b64decode(payload))["exp"]
        if exp - int(time.time()) < 60:
            new = xl_api.refresh_token(d["refresh_token"])
            if new and new.get("access_token"):
                d.update(new)
                save_tokens(d)
                print("🔄 Token di-refresh")
            else:
                print("❌ Token expired & refresh gagal — login ulang via OTP!")
                sys.exit(1)
    except Exception:
        pass
    return d


def search_package(id_token: str, keyword: str, max_price: int) -> dict | None:
    """Cari paket by keyword di store (v9 search)."""
    body = {
        "is_enterprise": False,
        "filters": [
            {"unit": "THOUSAND", "id": "FIL_SEL_P", "type": "PRICE", "items": []},
            {"unit": "GB", "id": "FIL_SEL_MQ", "type": "DATA_TYPE", "items": []},
            {"unit": "PACKAGE_NAME", "id": "FIL_PKG_N", "type": "PACKAGE_NAME", "items": [{"id": "", "label": ""}]},
            {"unit": "DAY", "id": "FIL_SEL_V", "type": "VALIDITY", "items": []},
        ],
        "substype": "PREPAID",
        "text_search": "",
        "lang": "en",
    }
    r = xl_api.send_api_request(id_token, "POST", "api/v9/xl-stores/options/search", body=body)
    if not r or r.get("status") != "SUCCESS":
        return None

    data = r.get("data", {})
    keyword_l = keyword.lower()
    best = None
    for key in ["results", "results_price_only"]:
        for p in data.get(key, []):
            title = p.get("title", "")
            if keyword_l in title.lower():
                price = p.get("original_price") or p.get("discounted_price") or 0
                if price > max_price:
                    continue
                if best is None or price < best.get("_price", 0):
                    best = {
                        "title": title,
                        "action_param": p.get("action_param", ""),
                        "price": price,
                        "family": p.get("family_name", ""),
                        "validity": p.get("validity", ""),
                    }
                    best["_price"] = price
    return best


def get_option_detail(id_token: str, search_code: str) -> dict | None:
    """Detail paket — return option_code asli + token_confirmation + price."""
    r = xl_api.send_api_request(id_token, "POST", "api/v8/xl-stores/options/detail", body={
        "package_option_code": search_code,
        "is_enterprise": False,
        "is_transaction_routine": False,
        "migration_type": "NONE",
        "family_role_hub": "",
        "is_autobuy": False,
        "is_shareable": False,
        "is_migration": False,
        "lang": "id",
        "is_upsell_pdp": False,
        "package_family_code": "",
        "package_variant_code": "",
    })
    if not r or r.get("status") != "SUCCESS":
        return None

    d = r.get("data", {})
    opt = d.get("package_option", {})
    fam = d.get("package_family", {})
    return {
        "option_code": opt.get("package_option_code", ""),
        "name": opt.get("name", ""),
        "price": opt.get("price", 0),
        "token_confirmation": d.get("token_confirmation", ""),
        "timestamp": d.get("timestamp", 0),
        "payment_for": fam.get("payment_for", "BUY_PACKAGE"),
    }


def pay_qris(tokens: dict, pkg: dict) -> str | None:
    """Eksekusi full flow QRIS. Return string QRIS (data untuk discan)."""
    id_token = tokens["id_token"]
    access_token = tokens["access_token"]
    option_code = pkg["option_code"]
    price = pkg["price"]
    token_confirmation = pkg["token_confirmation"]

    # 1. Intercept page
    print("1️⃣ intercept_page...")
    xl_api.intercept_page(id_token, option_code)

    # 2. Payment methods
    print("2️⃣ Ambil metode pembayaran...")
    pm = xl_api.get_payment_methods(id_token, option_code, token_confirmation)
    if not pm or pm.get("status") != "SUCCESS":
        print("❌ Gagal ambil metode pembayaran:", json.dumps(pm)[:200] if pm else None)
        return None
    pdata = pm.get("data", pm)
    token_payment = pdata.get("token_payment", "")
    ts_to_sign = pdata.get("timestamp", 0)
    if not token_payment:
        print("❌ token_payment kosong")
        return None

    # 3. Settlement QRIS
    print("3️⃣ Initiate settlement QRIS...")
    result = xl_api.purchase_qris(id_token, access_token, option_code, price,
                                  token_confirmation, token_payment, ts_to_sign)
    if not result or result.get("status") != "SUCCESS":
        print("❌ Settlement gagal:", json.dumps(result)[:300] if result else None)
        return None
    transaction_id = result.get("data", {}).get("transaction_code", "")
    print(f"   ✅ Transaction code: {transaction_id}")

    # 4. Fetch QRIS
    print("4️⃣ Ambil QR code...")
    qr_code = xl_api.get_qris_code(id_token, transaction_id)
    if not qr_code:
        print("❌ Gagal ambil QR code")
        return None
    return qr_code


def show_qr(qr_data: str, out_path: str):
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(out_path)
    print(f"📸 QR tersimpan: {out_path}")


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "Flex 13GB"
    max_price = int(sys.argv[2]) if len(sys.argv) > 2 else 50000

    tokens = ensure_token()
    id_token = tokens["id_token"]

    print(f"🔍 Cari paket: '{keyword}' (max Rp {max_price:,})")
    found = search_package(id_token, keyword, max_price)
    if not found:
        print("❌ Paket tidak ditemukan")
        return

    print(f"   Ditemukan: {found['title']} — Rp {found['price']:,} ({found['validity']})")
    detail = get_option_detail(id_token, found["action_param"])
    if not detail:
        print("❌ Gagal ambil detail paket")
        return
    pkg = {**found, **detail}
    print(f"   Detail: {pkg['name']} — Rp {pkg['price']:,}")

    qr_data = pay_qris(tokens, pkg)
    if not qr_data:
        print("❌ Flow QRIS gagal")
        return

    # Simpan data & tampilkan QR
    os.makedirs("data", exist_ok=True)
    with open("data/qris_last.txt", "w") as f:
        f.write(qr_data)
    out = os.path.join("data", "qris_payment.png")
    show_qr(qr_data, out)
    print(f"💳 Scan QR di: {out}")
    print("⏳ QRIS kadaluarsa ~5-15 menit. Setelah bayar, paket masuk otomatis.")


if __name__ == "__main__":
    main()
