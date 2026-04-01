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
import httpx
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
PRICE_SANITY_PCT = 0.10      # %10'dan fazla fark varsa reddet

# Canlı fiyat verileri
live_prices: dict = {}
_stream_running = False
_last_update = 0

# ─── Investing.com fiyat kontrolü ────────────────────────────────────────────

INVESTING_SYMBOLS = {
    "USDTRY": "currencies/usd-try",
    "EURTRY": "currencies/eur-try",
    "EURUSD": "currencies/eur-usd",
    "XAUUSD": "commodities/gold",
    "THYAO":  "equities/turk-hava-yollari",
    "GARAN":  "equities/garanti-bankasi",
    "AKBNK":  "equities/akbank",
}

_investing_cache: dict = {}   # {symbol: (price, ts)}
INVESTING_CACHE_TTL = 30      # 30 saniyede bir yenile

async def get_investing_price(symbol: str) -> float | None:
    """Investing.com'dan referans fiyat çek (cache'li)."""
    now = time.time()
    cached = _investing_cache.get(symbol)
    if cached and now - cached[1] < INVESTING_CACHE_TTL:
        return cached[0]

    path = INVESTING_SYMBOLS.get(symbol)
    if not path:
        return None

    try:
        url = f"https://api.investing.com/api/financialdata/historical/{path}"
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"https://www.investing.com/{path}",
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True,
            )
            # Sayfadaki son fiyatı bul
            import re as _re
            match = _re.search(r'"last":"?([\d.]+)"?', r.text)
            if match:
                price = float(match.group(1))
                _investing_cache[symbol] = (price, now)
                return price
    except Exception as e:
        logger.debug(f"Investing.com fiyat alınamadı ({symbol}): {e}")
    return None


# Yahoo Finance referans fiyatları (başlangıç için)
_yahoo_cache: dict = {}
YAHOO_CACHE_TTL = 300  # 5 dakika

async def get_yahoo_price(symbol: str) -> float | None:
    """Yahoo Finance'ten referans fiyat çek."""
    now = time.time()
    cached = _yahoo_cache.get(symbol)
    if cached and now - cached[1] < YAHOO_CACHE_TTL:
        return cached[0]
    
    # Sembol dönüşüm
    yahoo_map = {
        "USDTRY": "USDTRY=X", "EURTRY": "EURTRY=X", "EURUSD": "EURUSD=X",
        "XAUUSD": "GC=F", "GLDGR": "GC=F",
        "THYAO": "THYAO.IS", "GARAN": "GARAN.IS", "AKBNK": "AKBNK.IS",
        "ISCTR": "ISCTR.IS", "YKBNK": "YKBNK.IS", "TUPRS": "TUPRS.IS",
        "TCELL": "TCELL.IS", "ARCLK": "ARCLK.IS", "BIMAS": "BIMAS.IS",
    }
    yahoo_sym = yahoo_map.get(symbol, symbol + ".IS")
    
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1m&range=1d"
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                price = data.get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice")
                if price and price > 0:
                    _yahoo_cache[symbol] = (float(price), now)
                    return float(price)
    except Exception as e:
        logger.debug(f"Yahoo fiyat alınamadı ({symbol}): {e}")
    return None


def sanity_check(symbol: str, new_price: float) -> bool:
    """Yeni fiyat önceki fiyatla %PRICE_SANITY_PCT'den fazla farklıysa reddet."""
    existing = live_prices.get(symbol, {})
    old_price = existing.get("last")
    
    if not old_price or old_price <= 0:
        # İlk fiyat — Yahoo cache'i varsa karşılaştır
        yahoo = _yahoo_cache.get(symbol)
        if yahoo:
            ref = yahoo[0]
            diff = abs(new_price - ref) / ref
            if diff > 0.20:  # Yahoo'dan %20'den fazla farklıysa reddet
                logger.warning(f"İlk fiyat sanity (Yahoo): {symbol} yahoo={ref} matriks={new_price} (%{diff*100:.1f}) — reddedildi")
                return False
        return True  # Yahoo yok, kabul et
    
    diff = abs(new_price - old_price) / old_price
    if diff > PRICE_SANITY_PCT:
        logger.warning(f"Sanity check FAILED: {symbol} {old_price} → {new_price} (%{diff*100:.1f} fark) — reddedildi")
        return False
    return True


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
                            new_price = decoded["last"]
                            # Sanity check — %10'dan fazla fark varsa reddet
                            if sanity_check(sym, new_price):
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

    # Başlangıçta Yahoo'dan referans fiyatları pre-load et
    preload_symbols = ["USDTRY","EURTRY","EURUSD","XAUUSD","THYAO","GARAN","AKBNK","ISCTR","TUPRS","TCELL"]
    for sym in preload_symbols:
        try:
            price = await get_yahoo_price(sym)
            if price:
                logger.info(f"Yahoo referans: {sym} = {price}")
        except:
            pass

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
