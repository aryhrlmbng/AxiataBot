# AxiataBot 🤖

**Bot Discord + CLI untuk mengelola akun XL Axiata (myXL) secara otomatis.**

> Reverse-engineered dari aplikasi myXL Android v8.9.1 — semua enkripsi & signature ditangani secara lokal, tanpa service pihak ketiga.

---

## ✨ Fitur

### Discord Bot (`bot.py`)
| Command | Fungsi |
|---------|--------|
| `/login 628xxxx` | Minta OTP login via SMS |
| `/otp 123456` | Verifikasi OTP & simpan sesi |
| `/logout` | Hapus sesi |
| `/saldo` | Cek pulsa & masa aktif |
| `/kuota` | Detail kuota per paket |
| `/paket` | Daftar kategori/family paket |
| `/paket_lihat <family>` | Isi paket dalam satu kategori |
| `/beli <code> [ya]` | Beli paket via saldo (dengan konfirmasi) |
| `/status` | Status sesi bot |

### CLI QRIS (`buy_qris.py`)
```bash
python buy_qris.py "Flex 13" 50000
```
Cari paket → generate QRIS → scan & bayar. Paket masuk otomatis setelah pembayaran.

### API Library (`xl_api.py`)
Python library untuk mengakses API XL Axiata secara langsung:
- ✅ OTP Login & refresh token
- ✅ Cek profil, saldo, kuota
- ✅ Cari & filter paket (v9 store search)
- ✅ Beli via saldo, e-wallet, QRIS
- ✅ Semua enkripsi & signature lokal

---

## 📋 Prasyarat

- Python 3.11+
- [Discord Bot Token](https://discord.com/developers/applications) (untuk mode bot)

## 🔧 Instalasi

```bash
# Clone
git clone https://github.com/aryhrlmbng/AxiataBot.git
cd AxiataBot

# Virtual env
python -m venv venv
source venv/Scripts/activate    # Windows
# source venv/bin/activate      # Linux/Mac

# Dependencies
pip install -r requirements.txt
```

## ⚙️ Konfigurasi

```bash
cp .env.example .env
# Isi DISCORD_BOT_TOKEN dengan token dari Discord Developer Portal
```

## 🚀 Menjalankan

### Discord Bot
```bash
python bot.py
```

### QRIS Purchase (CLI)
```bash
python buy_qris.py "Flex 13" 50000
# Cari: "Flex 13" — harga maks: Rp 50.000
```

## 🏗️ Struktur Proyek

```
AxiataBot/
├── bot.py            # Discord bot (9 slash commands)
├── buy_qris.py       # CLI QRIS purchase flow
├── xl_api.py         # XL Axiata API client (auth, kuota, paket, purchase)
├── xl_crypto.py      # Kriptografi lokal (AES, HMAC, signature)
├── sessions.py       # Session storage (JSON)
├── .env.example      # Template konfigurasi
├── .gitignore
└── requirements.txt
```

## 🔬 API Endpoints (Reverse Engineering)

### CIAM Auth (`gede.ciam.xlaxiata.co.id`)
| Endpoint | Fungsi |
|----------|--------|
| `GET /realms/xl-ciam/auth/otp` | Minta OTP |
| `POST /realms/xl-ciam/protocol/openid-connect/token` | Submit OTP → OAuth tokens |
| `POST /realms/xl-ciam/protocol/openid-connect/token` | Refresh token |

### Business API (`api.myxl.xlaxiata.co.id`)
| Endpoint | Metode | Fungsi |
|----------|--------|--------|
| `api/v8/profile` | POST | Profil akun |
| `api/v8/packages/balance-and-credit` | POST | Saldo & info kredit |
| `api/v8/packages/quota-details` | POST | Detail kuota |
| `api/v9/xl-stores/options/search` | POST | Cari paket (dengan filter) |
| `api/v8/xl-stores/options/detail` | POST | Detail paket (token_confirmation) |
| `payments/api/v8/payment-methods-option` | POST | Metode pembayaran |
| `payments/api/v8/settlement-multipayment/qris` | POST | Settlement QRIS |
| `payments/api/v8/pending-detail` | POST | Ambil QR code |

### Otentikasi
- Login: OTP via SMS → Keycloak OAuth2 (grant_type=password)
- Body: AES-256-CBC dengan key `5dccbf08920a5527b99e222789c34bb7`
- Signature: `HMAC-SHA512` dengan base secret & id_token
- `Ax-Api-Signature`: `HMAC-SHA256` dengan key `18b4d589826af50241177961590e6693`

## ⚠️ Disclaimer

Proyek ini adalah alat unofficial yang berinteraksi dengan layanan web myXL untuk keperluan otomatisasi pribadi.

Tidak memodifikasi, melewati, atau mengeksploitasi mekanisme keamanan platform XL Axiata. Semua request dilakukan menggunakan interaksi HTTP standar seperti yang dilakukan oleh aplikasi resmi.

**Gunakan dengan risiko sendiri.**

## 📚 Referensi

- [0xtbug/telbot](https://github.com/0xtbug/telbot) — Telkomsel bot (inspirasi utama)
- [dalifajr/xl-cli-main](https://github.com/dalifajr/xl-cli-main) — XL CLI reference
- [Banday-Wrt/myxl-cli](https://github.com/Banday-Wrt/myxl-cli) — XL CLI reference
- [Ghalihx/myxl-telegram-bot](https://github.com/Ghalihx/myxl-telegram-bot) — XL Telegram bot reference