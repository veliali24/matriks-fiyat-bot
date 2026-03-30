"""
Matriks Gerçek Zamanlı Fiyat Stream
- Playwright ile session açar, WS token alır
- Direkt WS bağlantısı ile fiyatları çeker
- 5 saniyede bir günceller
- Hesap rotasyonu ile bloke önler
"""

import asyncio
import json
import struct
import re
import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from account_manager import rotator, load_accounts, save_account
from decode_proto import decode_mx_message

load_dotenv()

logger = logging.getLogger(__name__)

MATRIKS_URL = "https://app.matrikswebtrader.com/tr/main"

# Canlı fiyat verileri
live_prices: dict = {}
_stream_running = False
_last_update = 0



async def get_session(username: str, password: str) -> dict | None:
    """Playwright ile Matriks'e giriş yapıp session ve WS bilgilerini alır."""
    from playwright.async_api import async_playwright
    
    result = {"session_key": None, "ws_url": None, "headers": {}}
    
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
            if "rtstream" in ws.url and "market" in ws.url:
                result["ws_url"] = ws.url
                logger.info(f"WS yakalandı: {ws.url}")

                async def on_frame(payload):
                    nonlocal msg_count
                    if isinstance(payload, bytes):
                        # İlk 100 mesajda topic'i logla (debug)
                        if msg_count < 100:
                            try:
                                text = payload.decode('latin-1')
                                import re as _re
                                topics = _re.findall(r'mx/[^\x00-\x1f]{3,40}', text)
                                if topics:
                                    logger.info(f"[frame {msg_count}] topics: {topics[:3]}")
                            except:
                                pass
                        decoded = decode_mx_message(payload)
                        if decoded and decoded.get("last"):
                            sym = decoded["symbol"]
                            live_prices[sym] = {**decoded, "ts": int(time.time())}
                            logger.info(f"Fiyat: {sym} = {decoded.get('last')}")
                        msg_count += 1

                ws.on("framereceived", lambda p: asyncio.ensure_future(on_frame(p)))

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
            # Farklı selector'ları dene
            selectors = [
                'input[name="mxcustom1"]',
                'input[type="text"]',
                'input[placeholder*="ullanıcı"]',
                'input[placeholder*="ser"]',
                '#username',
                '#mxcustom1',
            ]
            login_input = None
            for sel in selectors:
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    login_input = sel
                    logger.info(f"Login input bulundu: {sel}")
                    break
                except:
                    continue
            if not login_input:
                await page.screenshot(path="debug_no_input.png")
                logger.error("Login input bulunamadı! debug_no_input.png'e bak.")
                await browser.close()
                return None
            pass_selectors = ['input[name="mxcustom2"]', 'input[type="password"]', '#password', '#mxcustom2']
            pass_input = 'input[name="mxcustom2"]'
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
        except Exception as e:
            logger.error(f"Giriş hatası ({username}): {e}")
            await browser.close()
            return None
        
        # Tarayıcıyı açık tut, WS intercept devam etsin
        logger.info(f"Session alındı: {username} → {result['session_key'][:8] if result['session_key'] else '?'}...")
        
        # 25 dakika bekle (session süresi)
        await page.wait_for_timeout(25 * 60 * 1000)
        
        await browser.close()
    
    return result if result["session_key"] else None


async def stream_prices(session_data: dict):
    """Artık kullanılmıyor — stream Playwright içinden intercept ediliyor."""
    pass


async def price_stream_loop():
    """Ana stream döngüsü — session yönetimi + WS bağlantısı."""
    global _stream_running
    _stream_running = True
    
    logger.info("Fiyat stream başlatılıyor...")
    
    while _stream_running:
        accounts = load_accounts()
        if not accounts:
            logger.warning("Hiç hesap yok! /hesap_ekle komutunu kullan.")
            await asyncio.sleep(30)
            continue
        
        # Mevcut hesabı al
        account = rotator.get_current()
        if not account:
            await asyncio.sleep(10)
            continue
        
        logger.info(f"Hesap kullanılıyor: {account['username']}")
        
        # Session al
        session_data = await get_session(account["username"], account["password"])
        
        if not session_data:
            logger.error(f"Session alınamadı: {account['username']} — döndürülüyor")
            rotator.rotate()
            await asyncio.sleep(10)
            continue
        
        rotator.set_session(session_data["session_key"], session_data["ws_url"])
        
        # WS stream başlat
        await stream_prices(session_data)
        
        # Bağlantı kesildi — 25dk dolmuşsa veya hata varsa döndür
        if rotator.is_session_expired():
            logger.info("Session süresi doldu, hesap döndürülüyor...")
            rotator.rotate()
        
        await asyncio.sleep(5)


def get_price(symbol: str) -> dict | None:
    """Belirli bir sembolün son fiyatını döner."""
    return live_prices.get(symbol.upper())


def get_all_prices() -> dict:
    """Tüm fiyatları döner."""
    return dict(live_prices)
