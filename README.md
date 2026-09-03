# AxiataBot 🤖

**Tool untuk mengelola akun XL Axiata (myXL) secara otomatis.**

> Reverse-engineered dari aplikasi myXL Android v8.9.1 — semua enkripsi & signature ditangani secara lokal, tanpa service pihak ketiga.

---

## ✨ Fitur

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

## 🚀 Menjalankan

### QRIS Purchase (CLI)
```bash
python buy_qris.py "Flex 13" 50000
# Cari: "Flex 13" — harga maks: Rp 50.000
```

## 🏗️ Struktur Proyek

```
AxiataBot/
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

## ⚠️ Disclaimer

Proyek ini adalah alat unofficial yang berinteraksi dengan layanan web myXL untuk keperluan otomatisasi pribadi.

Tidak memodifikasi, melewati, atau mengeksploitasi mekanisme keamanan platform XL Axiata. Semua request dilakukan menggunakan interaksi HTTP standar seperti yang dilakukan oleh aplikasi resmi.

**Gunakan dengan risiko sendiri.**