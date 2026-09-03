"""
XL Discord Bot — kelola akun XL Axiata via Discord slash commands.
Login OTP, cek saldo/kuota, list & beli paket. Semua request ke XL lokal & terenkripsi.

Cara jalan:
    python bot.py
Env:
    DISCORD_BOT_TOKEN  (wajib)
    DISCORD_ADMIN_IDS  (opsional, koma-pisah ID Discord yang diizinkan; kosong = semua)
"""
import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import xl_api
from sessions import SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("xlbot")

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("DISCORD_ADMIN_IDS", "").split(",") if x.strip().isdigit()}

store = SessionStore()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def authorized(interaction: discord.Interaction) -> bool:
    """Cek apakah user diizinkan memakai bot (kosong = semua orang)."""
    return not ADMIN_IDS or interaction.user.id in ADMIN_IDS


async def ensure_token(user_id: int) -> tuple[str, dict] | None:
    """Ambil id_token; kalau expired coba refresh sekali. Return (number, tokens) atau None."""
    tokens = store.get_tokens(user_id)
    if not tokens or not tokens["id_token"]:
        return None
    return tokens["number"], tokens


def fmt_number(n: int | None) -> str:
    if n is None:
        return "-"
    return f"Rp {n:,.0f}".replace(",", ".")


def fmt_date(s: str | None) -> str:
    if not s:
        return "-"
    return s[:10]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        log.info("Bot ready as %s — synced %d commands", bot.user, len(synced))
    except Exception as e:
        log.error("Sync failed: %s", e)
        log.info("Bot ready as %s", bot.user)


@bot.tree.command(name="login", description="Minta OTP ke nomor XL (format 628xxxxxxxxxx)")
@app_commands.describe(nomor="Nomor XL, contoh 6281234567890")
async def cmd_login(interaction: discord.Interaction, nomor: str):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    contact = xl_api.validate_contact(nomor)
    if not contact:
        return await interaction.followup.send("❌ Format nomor salah. Harus dimulai `628` dan maksimal 14 digit.", ephemeral=True)

    subscriber_id = await asyncio.to_thread(xl_api.get_otp, contact)
    if not subscriber_id:
        return await interaction.followup.send(
            "❌ Gagal mengirim OTP. Cek nomornya / coba lagi nanti (bisa jadi rate-limit).", ephemeral=True
        )

    store.set_pending_otp(interaction.user.id, contact, subscriber_id)
    await interaction.followup.send(
        f"📲 OTP dikirim ke **{contact}**.\n"
        f"Balas dengan `/otp <kode 6 digit>` dalam 5 menit.",
        ephemeral=True,
    )


@bot.tree.command(name="otp", description="Verifikasi kode OTP yang diterima via SMS")
@app_commands.describe(kode="Kode OTP 6 digit dari SMS")
async def cmd_otp(interaction: discord.Interaction, kode: str):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    kode = kode.strip()
    if len(kode) != 6 or not kode.isdigit():
        return await interaction.followup.send("❌ Kode harus 6 digit angka.", ephemeral=True)

    pending = store.get_pending_otp(interaction.user.id)
    if not pending:
        return await interaction.followup.send(
            "❌ Tidak ada OTP pending. Jalankan `/login` dulu.", ephemeral=True
        )

    number = pending["number"]
    subscriber_id = pending.get("subscriber_id")

    tokens = await asyncio.to_thread(xl_api.submit_otp, number, kode, subscriber_id)
    if not tokens:
        return await interaction.followup.send(
            "❌ OTP salah / sudah kedaluwarsa. Jalankan `/login` lagi kalau perlu.", ephemeral=True
        )

    store.save_tokens(interaction.user.id, number, tokens)
    store.clear_pending_otp(interaction.user.id)
    await interaction.followup.send(
        f"✅ Berhasil login ke **{number}**!\n"
        f"Coba `/saldo` untuk cek saldo atau `/kuota` untuk lihat kuota.",
        ephemeral=True,
    )


@bot.tree.command(name="logout", description="Hapus sesi/login dari bot ini")
async def cmd_logout(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    store.logout(interaction.user.id)
    await interaction.response.send_message("👋 Sesi dihapus. Untuk login ulang, jalankan `/login`.", ephemeral=True)


@bot.tree.command(name="saldo", description="Cek saldo pulsa & credit")
async def cmd_saldo(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    tokens = store.get_tokens(interaction.user.id)
    if not tokens:
        return await interaction.followup.send("❌ Belum login. Jalankan `/login` dulu.", ephemeral=True)

    data = await asyncio.to_thread(xl_api.get_balance, tokens["id_token"])
    if data is None:
        return await interaction.followup.send(
            "❌ Gagal ambil saldo. Coba `/login` ulang (token mungkin expired).", ephemeral=True
        )

    # Struktur response balance-and-credit
    balances = data.get("data", {}).get("balances", []) if isinstance(data, dict) else []
    lines = [f"💳 **Saldo** — {tokens['number']}"]
    if not balances:
        lines.append("_(data saldo tidak ditemukan)_")
    for b in balances:
        lines.append(f"• **{b.get('name', 'Saldo')}**: {fmt_number(b.get('amount'))}")
        if b.get("expiry_date"):
            lines[-1] += f"  (exp {fmt_date(b['expiry_date'])})"
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="kuota", description="Cek detail kuota paket aktif")
async def cmd_kuota(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    tokens = store.get_tokens(interaction.user.id)
    if not tokens:
        return await interaction.followup.send("❌ Belum login. Jalankan `/login` dulu.", ephemeral=True)

    data = await asyncio.to_thread(xl_api.get_quota, tokens["id_token"])
    if data is None:
        return await interaction.followup.send(
            "❌ Gagal ambil kuota. Coba `/login` ulang.", ephemeral=True
        )

    quotas = data.get("data", {}).get("quotas", []) if isinstance(data, dict) else []
    if not quotas:
        return await interaction.followup.send("📭 Tidak ada paket aktif / kuota kosong.", ephemeral=True)

    lines = [f"📊 **Kuota** — {tokens['number']}"]
    for q in quotas[:20]:
        name = q.get("name") or q.get("quota_code") or "?"
        used = q.get("used") or 0
        total = q.get("total") or 0
        exp = fmt_date(q.get("expiry_date"))
        if total:
            lines.append(f"• **{name}**: {used}/{total} MB  (exp {exp})")
        else:
            lines.append(f"• **{name}**: {used} MB  (exp {exp})")
    if len(quotas) > 20:
        lines.append(f"_...dan {len(quotas) - 20} lagi_")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="paket", description="Daftar kategori/keluarga paket XL")
async def cmd_paket(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    tokens = store.get_tokens(interaction.user.id)
    if not tokens:
        return await interaction.followup.send("❌ Belum login. Jalankan `/login` dulu.", ephemeral=True)

    data = await asyncio.to_thread(xl_api.get_families, tokens["id_token"])
    if data is None:
        return await interaction.followup.send("❌ Gagal ambil daftar paket.", ephemeral=True)

    families = data.get("data", {}).get("families", []) if isinstance(data, dict) else []
    if not families:
        return await interaction.followup.send("📭 Tidak ada data paket.", ephemeral=True)

    lines = [f"🗂️ **Kategori Paket** ({len(families)})\n"]
    for f in families:
        code = f.get("family_code") or f.get("code") or "?"
        name = f.get("name") or f.get("family_name") or code
        lines.append(f"`{code}` — {name}")
    lines.append("\nKetik `/paket_lihat <family_code>` untuk lihat isinya.")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="paket_lihat", description="Lihat daftar paket dalam satu kategori")
@app_commands.describe(family_code="Kode family (dari /paket)")
async def cmd_paket_lihat(interaction: discord.Interaction, family_code: str):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    tokens = store.get_tokens(interaction.user.id)
    if not tokens:
        return await interaction.followup.send("❌ Belum login. Jalankan `/login` dulu.", ephemeral=True)

    data = await asyncio.to_thread(xl_api.get_options, tokens["id_token"], family_code.strip())
    if data is None:
        return await interaction.followup.send("❌ Gagal ambil paket. Cek kode family-nya.", ephemeral=True)

    options = data.get("data", {}).get("options", []) if isinstance(data, dict) else []
    if not options:
        return await interaction.followup.send("📭 Tidak ada paket dalam kategori ini.", ephemeral=True)

    lines = [f"📦 **Paket** `{family_code}` ({len(options)})\n"]
    for o in options[:25]:
        code = o.get("option_code") or o.get("code") or "?"
        name = o.get("name") or code
        price = fmt_number(o.get("price"))
        quota = o.get("quota") or ""
        lines.append(f"`{code}` — {name} | {price}{f' | {quota}' if quota else ''}")
    if len(options) > 25:
        lines.append(f"_...dan {len(options) - 25} lagi_")
    lines.append("\nKetik `/beli <option_code>` untuk membeli (via saldo).")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="beli", description="Beli paket via saldo — tambah 'ya' untuk konfirmasi")
@app_commands.describe(option_code="Kode paket (dari /paket_lihat)", konfirmasi="Tulis 'ya' untuk benar-benar beli")
async def cmd_beli(interaction: discord.Interaction, option_code: str, konfirmasi: str = ""):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)

    tokens = store.get_tokens(interaction.user.id)
    if not tokens:
        return await interaction.followup.send("❌ Belum login. Jalankan `/login` dulu.", ephemeral=True)

    # Ambil detail paket dulu biar ada harga + konfirmasi
    detail = await asyncio.to_thread(xl_api.get_option_detail, tokens["id_token"], option_code.strip())
    if detail is None:
        return await interaction.followup.send("❌ Gagal ambil detail paket. Cek kode-nya.", ephemeral=True)

    opt = None
    d = detail.get("data", {})
    if isinstance(d, dict):
        opt = d.get("option") or (d.get("options") or [None])[0]
    if not opt:
        return await interaction.followup.send("❌ Detail paket tidak ditemukan.", ephemeral=True)

    name = opt.get("name") or option_code
    price = opt.get("price") or 0
    token_confirmation = opt.get("token_confirmation") or ""

    if konfirmasi.strip().lower() != "ya":
        await interaction.followup.send(
            f"⚠️ **Konfirmasi pembelian**\n"
            f"Paket: **{name}**\nHarga: **{fmt_number(price)}** (via saldo)\n\n"
            f"Untuk membeli, jalankan `/beli {option_code} ya`",
            ephemeral=True,
        )
        return

    # --- Proses pembelian ---
    await interaction.followup.send(f"⏳ Proses **{name}**...", ephemeral=True)

    # 1. Intercept page (wajib)
    await asyncio.to_thread(xl_api.intercept_page, tokens["id_token"], option_code.strip())

    # 2. Dapatkan metode pembayaran → token_payment + timestamp
    pm = await asyncio.to_thread(xl_api.get_payment_methods, tokens["id_token"], option_code.strip(), token_confirmation)
    if not pm:
        return await interaction.followup.send("❌ Gagal ambil metode pembayaran.", ephemeral=True)

    pdata = pm.get("data", pm)
    token_payment = pdata.get("token_payment", "")
    ts_to_sign = pdata.get("timestamp", 0)
    if not token_payment:
        return await interaction.followup.send("❌ Tidak ada token_payment di response.", ephemeral=True)

    # 3. Purchase via balance
    result = await asyncio.to_thread(
        xl_api.purchase_balance,
        tokens["id_token"],
        tokens["access_token"],
        option_code.strip(),
        price,
        token_confirmation,
        token_payment,
        ts_to_sign,
    )
    if not result:
        return await interaction.followup.send("❌ Pembelian gagal (response kosong/null).", ephemeral=True)

    # 4. Baca hasil
    status = result.get("status", "?")
    msg = result.get("message") or result.get("data", {}).get("message", "")
    trx_id = result.get("data", {}).get("transaction_id") or result.get("data", {}).get("trx_id", "")
    parts = [
        "✅ **Pembelian selesai**" if status == "SUCCESS" else f"❌ **Pembelian gagal** (status: {status})",
        f"Paket: **{name}**",
        f"Harga: **{fmt_number(price)}**",
    ]
    if msg:
        parts.append(f"Pesan: {msg}")
    if trx_id:
        parts.append(f"ID Transaksi: `{trx_id}`")
    await interaction.followup.send("\n".join(parts), ephemeral=True)


@bot.tree.command(name="status", description="Status sesi bot & login")
async def cmd_status(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message("⛔ Kamu tidak diizinkan memakai bot ini.", ephemeral=True)
    s = store.get(interaction.user.id)
    if not s:
        text = "❌ Belum login."
    else:
        text = (
            f"✅ Login sebagai **{s.get('number')}**\n"
            f"Terakhir update: {s.get('updated_at')}\n"
            f"Token refresh tersimpan: {'ya' if s.get('refresh_token') else 'tidak'}"
        )
    await interaction.response.send_message(text, ephemeral=True)


# --------------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN belum diset. Salin .env.example ke .env dan isi token.")
        return
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
