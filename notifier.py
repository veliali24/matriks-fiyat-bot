"""
Admin bildirim sistemi.
Kritik olayları Telegram'a gönderir.
"""

import asyncio
import logging
import os
import time
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_bot = None
_admin_ids: list[int] = []

# Son bildirim zamanları (spam önleme)
_last_notif: dict[str, float] = {}
NOTIF_COOLDOWN = 600  # Aynı hata 10 dakikada bir bildirilir


def init(bot, admin_ids: list[int]):
    """Bot ve admin listesini kaydet."""
    global _bot, _admin_ids
    _bot = bot
    _admin_ids = admin_ids


def _can_notify(key: str) -> bool:
    """Cooldown kontrolü — aynı hatayı spam etme."""
    now = time.time()
    last = _last_notif.get(key, 0)
    if now - last > NOTIF_COOLDOWN:
        _last_notif[key] = now
        return True
    return False


async def notify(message: str, key: str = None, force: bool = False):
    """Admin'lere bildirim gönder."""
    if not _bot or not _admin_ids:
        return

    if key and not force and not _can_notify(key):
        return

    for admin_id in _admin_ids:
        try:
            await _bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Bildirim gönderilemedi ({admin_id}): {e}")


# ─── Hazır bildirim şablonları ────────────────────────────────────────────────

async def notify_stream_connected(ws_url: str):
    await notify(
        f"✅ *Stream bağlandı*\n`{ws_url}`",
        key="stream_connected",
    )


async def notify_stream_disconnected(reason: str):
    await notify(
        f"⚠️ *Stream kesildi*\nSebep: `{reason}`\nYeniden bağlanılıyor...",
        key="stream_disconnected",
    )


async def notify_session_failed(username: str, reason: str):
    await notify(
        f"❌ *Matriks oturumu başarısız*\nHesap: `{username}`\nSebep: `{reason}`",
        key=f"session_failed_{username}",
    )


async def notify_session_rotated(old: str, new: str):
    await notify(
        f"🔄 *Hesap döndürüldü*\n`{old}` → `{new}`",
        key="session_rotated",
    )


async def notify_no_accounts():
    await notify(
        f"🚨 *Hiç Matriks hesabı yok!*\n`/hesap_ekle` komutu ile hesap ekleyin.",
        key="no_accounts",
    )


async def notify_stale_data(age_seconds: int):
    await notify(
        f"⏱️ *Fiyat verisi güncel değil*\nSon güncelleme: `{age_seconds}` saniye önce\nStream kontrol ediliyor...",
        key="stale_data",
    )


async def notify_api_started(port: int):
    await notify(
        f"🚀 *Sistem başlatıldı*\nAPI: `http://localhost:{port}`\nStream: Bağlanıyor...",
        key="api_started",
        force=False,  # Cooldown uygula — restart spam'ını önle
    )


async def notify_api_error(error: str):
    await notify(
        f"💥 *Kritik hata*\n```\n{error[:500]}\n```",
        key="api_error",
    )
