"""
Yahoo Finance fiyat çekici
- yfinance ile batch çeker (tüm semboller tek istekte)
- Her 3 saniyede bir günceller
- live_prices dict'ini günceller
"""

import asyncio
import time
import logging
import yfinance as yf

logger = logging.getLogger(__name__)

# Yahoo sembol haritası
YAHOO_SYMBOLS = {
    # Döviz
    "USDTRY": "USDTRY=X",
    "EURTRY": "EURTRY=X",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GBPTRY": "GBPTRY=X",
    # Emtia
    "XAUUSD": "GC=F",      # Altın
    "GLDGR":  "GC=F",      # Altın gram (dönüştürülür)
    "XAGUSD": "SI=F",      # Gümüş
    # BIST hisseleri
    "THYAO":  "THYAO.IS",
    "GARAN":  "GARAN.IS",
    "AKBNK":  "AKBNK.IS",
    "ISCTR":  "ISCTR.IS",
    "YKBNK":  "YKBNK.IS",
    "HALKB":  "HALKB.IS",
    "VAKBN":  "VAKBN.IS",
    "TUPRS":  "TUPRS.IS",
    "ARCLK":  "ARCLK.IS",
    "BIMAS":  "BIMAS.IS",
    "ASELS":  "ASELS.IS",
    "EREGL":  "EREGL.IS",
    "KCHOL":  "KCHOL.IS",
    "SASA":   "SASA.IS",
    "TCELL":  "TCELL.IS",
    "TTKOM":  "TTKOM.IS",
    "FROTO":  "FROTO.IS",
    "TOASO":  "TOASO.IS",
    "PGSUS":  "PGSUS.IS",
    "SAHOL":  "SAHOL.IS",
    # Kripto
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}

# TRY/oz → TRY/gram dönüşüm için USDTRY lazım
USDTRY_FALLBACK = 44.5

yahoo_prices: dict = {}  # {symbol: {last, prev, chg_pct, ts, source}}
_yahoo_running = False


def _fetch_batch() -> dict:
    """Tüm sembolleri tek yfinance çağrısıyla çek."""
    result = {}
    try:
        yahoo_list = list(set(YAHOO_SYMBOLS.values()))
        tickers = yf.Tickers(" ".join(yahoo_list))
        
        for our_sym, yahoo_sym in YAHOO_SYMBOLS.items():
            try:
                info = tickers.tickers[yahoo_sym].fast_info
                last = info.last_price
                prev = info.previous_close
                
                if not last or last <= 0:
                    continue
                
                # GLDGR: altın ons → gram TRY dönüşümü
                if our_sym == "GLDGR":
                    usdtry = yahoo_prices.get("USDTRY", {}).get("last") or USDTRY_FALLBACK
                    last = round(last * usdtry / 31.1035, 2)  # oz→gram, USD→TRY
                    prev_raw = info.previous_close
                    prev = round(prev_raw * usdtry / 31.1035, 2) if prev_raw else None
                
                chg_pct = None
                if prev and prev > 0:
                    chg_pct = round((last - prev) / prev * 100, 2)
                
                result[our_sym] = {
                    "last":     round(last, 4),
                    "prev":     round(prev, 4) if prev else None,
                    "chg_pct":  chg_pct,
                    "ts":       int(time.time()),
                    "source":   "yahoo",
                }
            except Exception as e:
                logger.debug(f"Yahoo {our_sym} ({yahoo_sym}): {e}")
                continue
    except Exception as e:
        logger.warning(f"Yahoo batch fetch hatası: {e}")
    
    return result


async def yahoo_price_loop():
    """Ana Yahoo Finance döngüsü — 3sn'de bir günceller."""
    global _yahoo_running, yahoo_prices
    _yahoo_running = True
    logger.info("Yahoo Finance feed başlatıldı")
    
    while _yahoo_running:
        try:
            loop = asyncio.get_event_loop()
            fresh = await loop.run_in_executor(None, _fetch_batch)
            if fresh:
                yahoo_prices.update(fresh)
                logger.info(f"Yahoo: {len(fresh)} sembol güncellendi")
        except Exception as e:
            logger.warning(f"Yahoo loop hatası: {e}")
        
        await asyncio.sleep(3)


def get_yahoo_price_dict(symbol: str) -> dict | None:
    return yahoo_prices.get(symbol.upper())


def get_all_yahoo_prices() -> dict:
    return dict(yahoo_prices)
