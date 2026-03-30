"""
Matriks Fiyat Botu
Gerçek zamanlı hisse fiyatları — WebSocket üzerinden.

Komutlar:
  /fiyat THYAO       — anlık fiyat
  /liste             — takip listesi
  /ekle THYAO        — takip listesine ekle
  /çıkar THYAO       — takip listesinden çıkar
  /alarm THYAO 300   — fiyat alarmı kur
  /alarmlar          — aktif alarmlar
  /hesap_ekle        — Matriks hesabı ekle (admin)
  /hesaplar          — hesap listesi (admin)
"""

import asyncio
import logging
import os
import json
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from account_manager import save_account, delete_account, list_accounts
from price_stream import get_price, get_all_prices, price_stream_loop

load_dotenv()

import pathlib
pathlib.Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

ALLOWED_USER_IDS = set(
    int(uid.strip()) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip()
)
ALLOWED_GROUP_IDS = set(
    int(gid.strip()) for gid in os.getenv("ALLOWED_GROUP_IDS", "").split(",") if gid.strip()
)

WATCHLIST_FILE = Path("watchlist.json")
ALARMS_FILE = Path("alarms.json")


# ─── Yetki ───────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if chat_id in ALLOWED_GROUP_IDS:
        return True
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def is_admin(update: Update) -> bool:
    return update.effective_user.id in ALLOWED_USER_IDS


# ─── Watchlist ────────────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        return []
    return json.loads(WATCHLIST_FILE.read_text())


def save_watchlist(lst: list[str]):
    WATCHLIST_FILE.write_text(json.dumps(lst))


# ─── Alarmlar ─────────────────────────────────────────────────────────────────

def load_alarms() -> list[dict]:
    if not ALARMS_FILE.exists():
        return []
    return json.loads(ALARMS_FILE.read_text())


def save_alarms(alarms: list[dict]):
    ALARMS_FILE.write_text(json.dumps(alarms))


# ─── Fiyat formatı ────────────────────────────────────────────────────────────

def format_price(symbol: str, data: dict) -> str:
    last = data.get("last", "-")
    bid = data.get("bid", "-")
    ask = data.get("ask", "-")
    vol = data.get("vol", "-")
    chg = data.get("chg_pct", None)

    chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "-"
    chg_emoji = "🟢" if isinstance(chg, (int, float)) and chg >= 0 else "🔴"

    return (
        f"{chg_emoji} *{symbol}*\n"
        f"Son: `{last}`\n"
        f"Alış: `{bid}` | Satış: `{ask}`\n"
        f"Hacim: `{vol}`\n"
        f"Değişim: `{chg_str}`"
    )


# ─── Komutlar ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "📈 *Matriks Fiyat Botu*\n\n"
        "/fiyat THYAO — anlık fiyat\n"
        "/liste — takip listesi\n"
        "/ekle THYAO — takip et\n"
        "/çıkar THYAO — takipten çıkar\n"
        "/alarm THYAO 300 — fiyat alarmı\n"
        "/alarmlar — aktif alarmlar\n\n"
        "Veya sadece sembol yaz: `THYAO`",
        parse_mode="Markdown",
    )


async def fiyat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("❌ Örnek: `/fiyat THYAO`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper()
    data = get_price(symbol)

    if not data:
        await update.message.reply_text(
            f"⏳ *{symbol}* için henüz veri yok.\nStream başladıktan sonra tekrar dene.",
            parse_mode="Markdown"
        )
        return

    keyboard = [[
        InlineKeyboardButton("🔄 Yenile", callback_data=f"fiyat:{symbol}"),
        InlineKeyboardButton("➕ Takip Et", callback_data=f"ekle:{symbol}"),
    ]]
    await update.message.reply_text(
        format_price(symbol, data),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def liste_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    watchlist = load_watchlist()
    if not watchlist:
        await update.message.reply_text("📋 Takip listesi boş. `/ekle THYAO` ile ekle.", parse_mode="Markdown")
        return

    lines = []
    for sym in watchlist:
        data = get_price(sym)
        if data:
            chg = data.get("chg_pct", None)
            last = data.get("last", "?")
            chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else ""
            emoji = "🟢" if isinstance(chg, (int, float)) and chg >= 0 else "🔴"
            lines.append(f"{emoji} `{sym}` — `{last}` {chg_str}")
        else:
            lines.append(f"⏳ `{sym}` — veri bekleniyor")

    keyboard = [[InlineKeyboardButton("🔄 Yenile", callback_data="liste")]]
    await update.message.reply_text(
        "📋 *Takip Listesi*\n\n" + "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ekle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("❌ Örnek: `/ekle THYAO`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper()
    watchlist = load_watchlist()
    if symbol in watchlist:
        await update.message.reply_text(f"⚠️ `{symbol}` zaten listede.", parse_mode="Markdown")
        return

    watchlist.append(symbol)
    save_watchlist(watchlist)
    await update.message.reply_text(f"✅ `{symbol}` takip listesine eklendi.", parse_mode="Markdown")


async def cikar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("❌ Örnek: `/çıkar THYAO`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper()
    watchlist = load_watchlist()
    if symbol not in watchlist:
        await update.message.reply_text(f"⚠️ `{symbol}` listede değil.", parse_mode="Markdown")
        return

    watchlist.remove(symbol)
    save_watchlist(watchlist)
    await update.message.reply_text(f"✅ `{symbol}` listeden çıkarıldı.", parse_mode="Markdown")


async def alarm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Örnek: `/alarm THYAO 300`\n"
            "Fiyat 300'e ulaşınca bildirim gelir.",
            parse_mode="Markdown"
        )
        return

    symbol = context.args[0].upper()
    try:
        target = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Geçersiz fiyat.", parse_mode="Markdown")
        return

    alarms = load_alarms()
    alarms.append({
        "symbol": symbol,
        "target": target,
        "chat_id": update.effective_chat.id,
        "user_id": update.effective_user.id,
        "triggered": False,
    })
    save_alarms(alarms)
    await update.message.reply_text(
        f"🔔 Alarm kuruldu: *{symbol}* → `{target}`",
        parse_mode="Markdown"
    )


async def alarmlar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    alarms = [a for a in load_alarms() if a["chat_id"] == update.effective_chat.id and not a["triggered"]]
    if not alarms:
        await update.message.reply_text("🔕 Aktif alarm yok.")
        return

    lines = [f"🔔 `{a['symbol']}` → `{a['target']}`" for a in alarms]
    await update.message.reply_text(
        "*Aktif Alarmlar:*\n\n" + "\n".join(lines),
        parse_mode="Markdown"
    )


async def hesap_ekle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Sadece adminler hesap ekleyebilir.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ Kullanım: `/hesap_ekle KULLANICI SIFRE`",
            parse_mode="Markdown"
        )
        return

    username, password = context.args[0], context.args[1]
    is_new = save_account(username, password, update.effective_user.id)
    try:
        await update.message.delete()
    except:
        pass

    action = "eklendi" if is_new else "güncellendi"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ Hesap {action}: `{username}`",
        parse_mode="Markdown"
    )


async def hesaplar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    accounts = list_accounts()
    if not accounts:
        await update.message.reply_text("Hiç hesap yok.")
        return

    text = "👤 *Matriks Hesapları:*\n" + "\n".join(f"  • `{a}`" for a in accounts)
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Text handler ─────────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    text = update.message.text.strip().upper()
    if not text.isalnum() or not (2 <= len(text) <= 6):
        return

    data = get_price(text)
    if data:
        keyboard = [[
            InlineKeyboardButton("🔄 Yenile", callback_data=f"fiyat:{text}"),
            InlineKeyboardButton("➕ Takip Et", callback_data=f"ekle:{text}"),
        ]]
        await update.message.reply_text(
            format_price(text, data),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(
            f"⏳ *{text}* için henüz veri yok.",
            parse_mode="Markdown"
        )


# ─── Callback handler ─────────────────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_authorized(update):
        return

    action, *args = query.data.split(":")

    if action == "fiyat" and args:
        symbol = args[0]
        data = get_price(symbol)
        if data:
            keyboard = [[
                InlineKeyboardButton("🔄 Yenile", callback_data=f"fiyat:{symbol}"),
                InlineKeyboardButton("➕ Takip Et", callback_data=f"ekle:{symbol}"),
            ]]
            await query.edit_message_text(
                format_price(symbol, data),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await query.answer("⏳ Veri henüz yok.", show_alert=True)

    elif action == "ekle" and args:
        symbol = args[0]
        watchlist = load_watchlist()
        if symbol not in watchlist:
            watchlist.append(symbol)
            save_watchlist(watchlist)
            await query.answer(f"✅ {symbol} takip listesine eklendi.")
        else:
            await query.answer(f"⚠️ {symbol} zaten listede.")

    elif action == "liste":
        watchlist = load_watchlist()
        if not watchlist:
            await query.edit_message_text("📋 Takip listesi boş.")
            return

        lines = []
        for sym in watchlist:
            data = get_price(sym)
            if data:
                chg = data.get("chg_pct", None)
                last = data.get("last", "?")
                chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else ""
                emoji = "🟢" if isinstance(chg, (int, float)) and chg >= 0 else "🔴"
                lines.append(f"{emoji} `{sym}` — `{last}` {chg_str}")
            else:
                lines.append(f"⏳ `{sym}` — bekleniyor")

        keyboard = [[InlineKeyboardButton("🔄 Yenile", callback_data="liste")]]
        await query.edit_message_text(
            "📋 *Takip Listesi*\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ─── Alarm kontrol döngüsü ────────────────────────────────────────────────────

async def alarm_check_loop(app: Application):
    """Her 10 saniyede alarmları kontrol eder."""
    while True:
        await asyncio.sleep(10)
        alarms = load_alarms()
        changed = False

        for alarm in alarms:
            if alarm["triggered"]:
                continue
            data = get_price(alarm["symbol"])
            if not data:
                continue

            last = data.get("last")
            if last is None:
                continue

            target = alarm["target"]
            # Hedefe ulaştı mı? (±0.5% tolerans)
            if abs(last - target) / target <= 0.005 or last >= target:
                try:
                    await app.bot.send_message(
                        chat_id=alarm["chat_id"],
                        text=f"🔔 *Alarm!* `{alarm['symbol']}` → `{last}` (hedef: `{target}`)",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Alarm gönderilemedi: {e}")
                alarm["triggered"] = True
                changed = True

        if changed:
            save_alarms(alarms)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    asyncio.create_task(price_stream_loop())
    asyncio.create_task(alarm_check_loop(app))
    logger.info("Fiyat stream ve alarm kontrol başlatıldı.")


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN eksik! .env dosyasını kontrol et.")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fiyat", fiyat_command))
    app.add_handler(CommandHandler("liste", liste_command))
    app.add_handler(CommandHandler("ekle", ekle_command))
    app.add_handler(CommandHandler("cikar", cikar_command))
    app.add_handler(CommandHandler("alarm", alarm_command))
    app.add_handler(CommandHandler("alarmlar", alarmlar_command))
    app.add_handler(CommandHandler("hesap_ekle", hesap_ekle_command))
    app.add_handler(CommandHandler("hesaplar", hesaplar_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Matriks Fiyat Botu başlatılıyor...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
