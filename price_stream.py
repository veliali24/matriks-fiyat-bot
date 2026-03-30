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

load_dotenv()

logger = logging.getLogger(__name__)

MATRIKS_URL = "https://app.matrikswebtrader.com/tr/main"

# Canlı fiyat verileri
live_prices: dict = {}
_stream_running = False
_last_update = 0


def decode_mx_message(raw: bytes) -> dict | None:
    """Binary Matriks mesajını decode eder."""
    try:
        if not raw or len(raw) < 5:
            return None
        
        text = raw.decode('latin-1')
        
        # Sembol
        m = re.search(r'mx/symbol/([A-Z0-9]+)@lvl2', text)
        is_deriv = False
        if not m:
            m = re.search(r'mx/derivative/([A-Z0-9]+)', text)
            is_deriv = True
        if not m:
            return None
        
        sym = m.group(1)
        
        # Protobuf field tag -> isim
        field_map = {
            0x29: 'last', 0x31: 'bid', 0x39: 'ask',
            0x41: 'high', 0x49: 'low', 0x51: 'open',
            0x59: 'prev', 0x61: 'chg', 0x69: 'chg_pct',
            0x71: 'vol', 0x79: 'tvol',
        }
        
        vals = {}
        for i in range(len(raw) - 8):
            tag = raw[i]
            if tag in field_map and i + 9 <= len(raw):
                try:
                    v = struct.unpack_from('<d', raw, i + 1)[0]
                    if 0.001 < abs(v) < 10_000_000 and v == v:
                        vals[field_map[tag]] = round(v, 4)
                except:
                    pass
        
        if not vals:
            return None
        
        return {
            "symbol": sym,
            "type": "derivative" if is_deriv else "stock",
            "ts": int(time.time()),
            **vals
        }
    except:
        return None


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
        
        async def on_ws(ws):
            if "rtstream" in ws.url and "market" in ws.url:
                result["ws_url"] = ws.url
        
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
            # Debug: sayfa yüklendikten sonra screenshot al
            await page.screenshot(path="debug_login.png")
            logger.info("Debug screenshot kaydedildi: debug_login.png")
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
            await page.screenshot(path="debug_after_login.png")
            await page.wait_for_selector('text=ARAÇLAR', timeout=30000)
        except Exception as e:
            logger.error(f"Giriş hatası ({username}): {e}")
            await browser.close()
            return None
        
        # Cookies & local storage al
        cookies = await context.cookies()
        result["cookies"] = {c["name"]: c["value"] for c in cookies}
        
        await browser.close()
    
    if result["session_key"] and result["ws_url"]:
        logger.info(f"Session alındı: {username} → {result['session_key'][:8]}...")
        return result
    
    return None


async def stream_prices(session_data: dict):
    """WebSocket üzerinden fiyat verisi çeker."""
    global live_prices, _last_update
    
    import websockets
    
    ws_url = session_data["ws_url"]
    
    # Cookie header
    cookie_str = "; ".join(f"{k}={v}" for k, v in session_data.get("cookies", {}).items())
    headers = {"Cookie": cookie_str} if cookie_str else {}
    
    try:
        connect_kwargs = {}
        if headers:
            # websockets versiyonuna göre doğru parametre
            try:
                import websockets
                ver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
                if ver >= (10, 0):
                    connect_kwargs["additional_headers"] = headers
                else:
                    connect_kwargs["extra_headers"] = headers
            except:
                connect_kwargs["additional_headers"] = headers

        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            logger.info(f"WS bağlantısı kuruldu: {ws_url}")
            
            async for message in ws:
                if isinstance(message, bytes):
                    result = decode_mx_message(message)
                    if result and result.get("last"):
                        sym = result["symbol"]
                        live_prices[sym] = result
                        _last_update = time.time()
    except Exception as e:
        logger.warning(f"WS bağlantısı kesildi: {e}")


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
