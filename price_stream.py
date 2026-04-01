"""
Matriks Gerçek Zamanlı Fiyat Stream
- Playwright ile session açar, WS intercept eder
- Hesap rotasyonu ile bloke önler
- Admin bildirimleri: bağlantı, kesinti, hata
"""

import asyncio
import struct
import re
import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from account_manager import rotator, load_accounts, save_account
from decode_proto import decode_mx_message
import notifier

load_dotenv()

logger = logging.getLogger(__name__)

MATRIKS_URL = "https://app.matrikswebtrader.com/tr/main"
STALE_CHECK_INTERVAL = 120   # 2 dakikada bir stale kontrol
STALE_THRESHOLD = 60         # 60 sn güncellenmemişse stale

# Canlı fiyat verileri
live_prices: dict = {}
_stream_running = False
_last_update = 0


async def get_session(username: str, password: str) -> dict | None:
    """Playwright ile Matriks'e giriş yapıp WS intercept eder."""
    from playwright.async_api import async_playwright

    result = {"session_key": None, "ws_url": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-web-security",
            ]
        )
        context = await browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            java_script_enabled=True,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()
        msg_count = 0

        async def on_ws(ws):
            nonlocal msg_count
            if ("rtstream" in ws.url or "dlstream" in ws.url) and "market" in ws.url:
                result["ws_url"] = ws.url
                asyncio.ensure_future(notifier.notify_stream_connected(ws.url))

                async def on_frame(payload):
                    global _last_update
                    nonlocal msg_count
                    if isinstance(payload, bytes):
                        decoded = decode_mx_message(payload)
                        if decoded and decoded.get("last"):
                            sym = decoded["symbol"]
                            existing = live_prices.get(sym, {})
                            live_prices[sym] = {**existing, **decoded, "ts": int(time.time())}
                            _last_update = time.time()
                        msg_count += 1

                async def on_close(ws):
                    asyncio.ensure_future(
                        notifier.notify_stream_disconnected("WebSocket kapandı")
                    )

                ws.on("framereceived", lambda p: asyncio.ensure_future(on_frame(p)))
                ws.on("close", on_close)

        page.on("websocket", on_ws)

        async def on_response(response):
            if "Integration.aspx" in response.url:
                try:
                    body = await response.json()
                    sk = body.get("Result", {}).get("SessionKey")
                    if sk:
                        result["session_key"] = sk
                except:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(MATRIKS_URL, wait_until="load", timeout=40000)
            await page.wait_for_timeout(5000)

            selectors = [
                'input[name="mxcustom1"]',
                'input[type="text"]',
                '#username',
                '#mxcustom1',
            ]
            login_input = None
            for sel in selectors:
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    login_input = sel
                    break
                except:
                    continue

            if not login_input:
                await notifier.notify_session_failed(username, "Login formu bulunamadı")
                await browser.close()
                return None

            pass_selectors = ['input[name="mxcustom2"]', 'input[type="password"]', '#password']
            pass_input = pass_selectors[0]
            for sel in pass_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=3000)
                    pass_input = sel
                    break
                except:
                    continue

            await page.fill(login_input, username)
            await page.fill(pass_input, password)
            await page.press(pass_input, 'Enter')
            await page.wait_for_timeout(10000)
            await page.wait_for_selector('text=ARAÇLAR', timeout=30000)

            # Giriş başarılı — Fiyat tablosunu aç (tüm BIST sembolleri stream olsun)
            try:
                await page.click('text=ARAÇLAR')
                await page.wait_for_timeout(500)
                # Fiyat Tablosu veya Piyasa menüsünü ara
                for menu_item in ['Fiyat Tablosu', 'Piyasa', 'Market', 'Hisse']:
                    try:
                        await page.click(f'text={menu_item}', timeout=3000)
                        await page.wait_for_timeout(1000)
                        logger.info(f"Menü açıldı: {menu_item}")
                        break
                    except:
                        continue
                # ESC ile popup'ları kapat
                await page.keyboard.press('Escape')
                await page.wait_for_timeout(500)
            except Exception as e:
                logger.warning(f"Fiyat tablosu açma hatası (devam ediliyor): {e}")

        except Exception as e:
            await notifier.notify_session_failed(username, str(e)[:200])
            await browser.close()
            return None

        # 25 dakika açık tut (session süresi)
        await page.wait_for_timeout(25 * 60 * 1000)
        await browser.close()

    return result if result["session_key"] else None


async def stale_monitor():
    """Periyodik olarak fiyat güncelliğini kontrol eder."""
    global _last_update
    await asyncio.sleep(60)  # Başlangıçta 1 dk bekle
    while True:
        await asyncio.sleep(STALE_CHECK_INTERVAL)
        if _last_update:
            age = int(time.time() - _last_update)
            if age > STALE_THRESHOLD:
                await notifier.notify_stale_data(age)


async def price_stream_loop():
    """Ana stream döngüsü — session yönetimi."""
    global _stream_running
    _stream_running = True

    # Stale monitor başlat
    asyncio.ensure_future(stale_monitor())

    while _stream_running:
        accounts = load_accounts()
        if not accounts:
            await notifier.notify_no_accounts()
            await asyncio.sleep(30)
            continue

        account = rotator.get_current()
        if not account:
            await asyncio.sleep(10)
            continue

        prev_account = account["username"]
        session_data = await get_session(account["username"], account["password"])

        if not session_data:
            rotator.rotate()
            new_account = rotator.get_current()
            if new_account and new_account["username"] != prev_account:
                await notifier.notify_session_rotated(
                    prev_account, new_account["username"]
                )
            await asyncio.sleep(10)
            continue

        rotator.set_session(session_data["session_key"], session_data.get("ws_url", ""))

        # Session bitti — döndür
        if rotator.is_session_expired():
            rotator.rotate()
            new_account = rotator.get_current()
            if new_account:
                await notifier.notify_session_rotated(
                    account["username"], new_account["username"]
                )

        await asyncio.sleep(5)


def get_price(symbol: str) -> dict | None:
    return live_prices.get(symbol.upper())


def get_all_prices() -> dict:
    return dict(live_prices)
